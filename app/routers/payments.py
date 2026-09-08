import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List
from fastapi import APIRouter, HTTPException, status, Depends, Request, Response

from app.core.config import settings
from app.core.security import get_current_user
from app.core.circuit_breaker import require_route_enabled
from app.db.firebase import db
from app.services.pricing import compute_quote, to_paise, SUBSCRIPTION_DAYS
from app.services import razorpay_service as rzp
from app.services import growth_service as gs
from app.models.schemas import CommonResponse

router = APIRouter(prefix="/api/v1", tags=["Payments & Subscriptions"])


@router.post(
    "/payments/create-order",
    dependencies=[Depends(require_route_enabled("payments"))]
)
async def create_payment_order(
    request_body: Dict[str, Any],
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    uid = current_user.get("sub")
    user = await db.get_user_by_id(uid) or {}
    access_type = user.get("access_type", "free")

    if access_type == "offline-comp":
        quote = await gs.user_quote(uid)
        return {
            "order_id": f"order_offline_{uuid.uuid4().hex[:10]}",
            "amount_paise": 0,
            "currency": "INR",
            "razorpay_order_id": None,
            "skip_payment": True,
            "quote": quote,
        }

    coupon_code = request_body.get("coupon_code", "").strip()
    if coupon_code:
        result = await gs.redeem_coupon(uid, coupon_code)
        if result.get("success"):
            quote = await gs.user_quote(uid)
            return {
                "order_id": f"order_coupon_{uuid.uuid4().hex[:10]}",
                "amount_paise": 0,
                "currency": "INR",
                "razorpay_order_id": None,
                "skip_payment": True,
                "quote": quote,
            }

    quote = await gs.user_quote(uid)
    amount_paise = quote["total_amount_paise"]

    if amount_paise <= 0:
        return {
            "order_id": f"order_free_{uuid.uuid4().hex[:10]}",
            "amount_paise": 0,
            "currency": "INR",
            "razorpay_order_id": None,
            "skip_payment": True,
            "quote": quote,
        }

    receipt = f"rcpt_{uid}_{int(datetime.now(timezone.utc).timestamp())}"
    try:
        rzp_order = await rzp.create_order(amount_paise, receipt, {"uid": uid})
        local_order_id = rzp_order["id"]
    except RuntimeError:
        local_order_id = rzp.generate_local_order_id()

    tx_data = {
        "order_id": local_order_id,
        "uid": uid,
        "amount": quote["total_amount"],
        "amount_paise": amount_paise,
        "base_amount": quote["base_amount"],
        "discount_amount": quote["discount_amount"],
        "gst_amount": quote["gst_amount"],
        "plan_id": quote["plan_id"],
        "currency": "INR",
        "status": "pending",
        "credit_applied": quote["discount_amount"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.create_transaction(tx_data)

    return {
        "order_id": local_order_id,
        "amount_paise": amount_paise,
        "currency": "INR",
        "razorpay_order_id": local_order_id if rzp.is_configured() else None,
        "skip_payment": False,
        "quote": quote,
    }


@router.post("/payments/verify")
async def verify_payment(
    payload: Dict[str, str],
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    uid = current_user.get("sub")
    razorpay_order_id = payload.get("razorpay_order_id", "")
    razorpay_payment_id = payload.get("razorpay_payment_id", "")
    razorpay_signature = payload.get("razorpay_signature", "")

    if not all([razorpay_order_id, razorpay_payment_id, razorpay_signature]):
        raise HTTPException(status_code=400, detail="Missing payment verification fields.")

    if rzp.is_configured():
        if not rzp.verify_payment_signature(razorpay_order_id, razorpay_payment_id, razorpay_signature):
            raise HTTPException(status_code=400, detail="Invalid payment signature.")
    else:
        if not razorpay_signature.startswith("dev_sig_"):
            pass

    await _fulfill_payment(uid, razorpay_order_id, razorpay_payment_id)
    return {"success": True, "message": "Payment verified and subscription activated."}


@router.post("/payments/razorpay/webhook")
async def razorpay_webhook(request: Request):
    raw_body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")

    if rzp.is_configured() and settings.RAZORPAY_WEBHOOK_SECRET:
        if not rzp.verify_webhook_signature(raw_body, signature):
            raise HTTPException(status_code=400, detail="Invalid webhook signature.")

    import json
    try:
        event = json.loads(raw_body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    event_type = event.get("event", "")
    payload_entity = event.get("payload", {}).get("payment", {}).get("entity", {})

    if event_type == "payment.captured":
        order_id = payload_entity.get("order_id", "")
        payment_id = payload_entity.get("id", "")
        notes = payload_entity.get("notes", {})
        uid = notes.get("uid", "")

        if not uid:
            if db.use_live_firestore:
                txns = db.collection("transactions").where("order_id", "==", order_id).stream()
                for doc in txns:
                    uid = doc.to_dict().get("uid")
                    break
            else:
                for u_txns in db._memory_transactions.values():
                    for tx in u_txns:
                        if tx.get("order_id") == order_id:
                            uid = tx.get("uid")
                            break
                    if uid:
                        break

        if uid:
            existing = db.collection("referral_redemptions").where("payment_id", "==", payment_id).stream() if db.use_live_firestore else []
            already_processed = False
            for _ in existing:
                already_processed = True
                break
            if not already_processed:
                await _fulfill_payment(uid, order_id, payment_id)

    return CommonResponse(success=True, message="Webhook processed.")


async def _fulfill_payment(uid: str, order_id: str, payment_id: str):
    expiry_date = (datetime.now(timezone.utc) + timedelta(days=SUBSCRIPTION_DAYS)).isoformat()
    await db.update_user(uid, {
        "is_premium": True,
        "subscription_expires_at": expiry_date,
        "access_type": "paid",
    })

    if db.use_live_firestore:
        txns = db.collection("transactions").where("order_id", "==", order_id).stream()
        for doc in txns:
            db.collection("transactions").document(doc.id).set({
                "status": "success",
                "razorpay_payment_id": payment_id,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }, merge=True)
    else:
        for u_txns in db._memory_transactions.values():
            for tx in u_txns:
                if tx.get("order_id") == order_id:
                    tx["status"] = "success"
                    tx["razorpay_payment_id"] = payment_id

    await gs.grant_referrer_credit_for_payment(uid, payment_id)
    await gs.convert_share_if_eligible(uid)


@router.get("/payments/history", response_model=List[Dict[str, Any]])
async def get_payment_history(current_user: Dict[str, Any] = Depends(get_current_user)):
    uid = current_user.get("sub")
    transactions = await db.get_transactions(uid)
    return transactions
