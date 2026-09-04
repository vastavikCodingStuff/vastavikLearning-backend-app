import hashlib
import hmac
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List
from fastapi import APIRouter, HTTPException, status, Depends, Request

from app.core.config import settings
from app.core.security import get_current_user
from app.core.circuit_breaker import require_route_enabled
from app.db.firebase import db
from app.models.schemas import (
    CreateOrderRequest,
    CreateOrderResponse,
    PaymentWebhookPayload,
    CommonResponse,
)

router = APIRouter(prefix="/api/v1", tags=["Payments & Subscriptions"])


@router.post(
    "/payments/create-order",
    response_model=CreateOrderResponse,
    dependencies=[Depends(require_route_enabled("payments"))]
)
async def create_payment_order(request: CreateOrderRequest, current_user: Dict[str, Any] = Depends(get_current_user)):
    """
    Creates a new subscription order and generates payment gateway signature token.
    """
    uid = current_user.get("sub")
    order_id = f"ORDER_{uuid.uuid4().hex[:10].upper()}"

    # Generate deterministic checksum
    secret = settings.PAYMENT_GATEWAY_SECRET or "default_vastavik_payment_secret_123"
    checksum_raw = f"{order_id}:{request.amount}:{uid}"
    checksum = hmac.new(secret.encode(), checksum_raw.encode(), hashlib.sha256).hexdigest()

    tx_data = {
        "order_id": order_id,
        "uid": uid,
        "amount": request.amount,
        "plan_id": request.plan_id,
        "currency": "INR",
        "status": "pending",
        "checksum": checksum,
    }
    await db.create_transaction(tx_data)

    return CreateOrderResponse(
        order_id=order_id,
        amount=request.amount,
        currency="INR",
        checksum=checksum,
        payment_url=f"https://api.vastaviklearning.com/checkout/{order_id}",
    )


@router.post(
    "/payments/webhook",
    response_model=CommonResponse,
    dependencies=[Depends(require_route_enabled("payments"))]
)
async def payment_webhook(payload: PaymentWebhookPayload, request: Request):
    """
    Receives and cryptographically verifies payment gateway webhooks.
    Automatically unlocks Vastavik Pro subscription status for the student upon success.
    """
    secret = settings.PAYMENT_GATEWAY_SECRET or "default_vastavik_payment_secret_123"
    expected_data = f"{payload.order_id}:{payload.status}"
    expected_sig = hmac.new(secret.encode(), expected_data.encode(), hashlib.sha256).hexdigest()

    # If webhook secret is configured, verify signature
    if settings.PAYMENT_GATEWAY_SECRET:
        if not hmac.compare_digest(payload.signature, expected_sig):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid webhook cryptographic signature.",
            )

    if payload.status == "success" and payload.uid:
        # Extend subscription by 30 days
        expiry_date = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        await db.update_user(payload.uid, {
            "is_premium": True,
            "subscription_expires_at": expiry_date,
        })

    return CommonResponse(success=True, message="Webhook processed successfully.")


@router.get("/payments/history", response_model=List[Dict[str, Any]])
async def get_payment_history(current_user: Dict[str, Any] = Depends(get_current_user)):
    """
    Returns complete transaction and invoice history for the student.
    """
    uid = current_user.get("sub")
    transactions = await db.get_transactions(uid)
    return transactions
