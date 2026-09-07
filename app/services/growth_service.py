import random
import secrets
import string
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.db.firebase import db
from app.services.pricing import (
    REFERRAL_REWARD_CAP,
    REFERRAL_REWARD_INR,
    SHARE_REWARD_CAP,
    SHARE_REWARD_INR,
    compute_quote,
)

CODE_PREFIX = "VK"
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
COUPON_DOC_ID = "offline_student"
SHARE_LINK_HOST = "https://vastavikcomputers.firebaseapp.com"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _round2(value: float) -> float:
    return round(value + 1e-9, 2)


def _query(coll_name: str, field: str, op: str, value: Any) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if db.use_live_firestore:
        from google.cloud.firestore_v1.base_query import FieldFilter
        coll = db.collection(coll_name)
        try:
            query = coll.where(filter=FieldFilter(field, op, value))
        except Exception:
            query = coll.where(field, op, value)
        for d in query.stream():
            data = d.to_dict() or {}
            data["id"] = d.id
            items.append(data)
        return items

    coll = db.collection(coll_name)
    for d in coll.stream():
        val = (d.to_dict() or {}).get(field)
        match = False
        if op == "==" and val == value:
            match = True
        elif op == "in" and isinstance(value, list) and val in value:
            match = True
        if match:
            data = d.to_dict() or {}
            data["id"] = d.id
            items.append(data)
    return items


def _save(coll_name: str, doc_id: str, data: Dict[str, Any]) -> None:
    coll = db.collection(coll_name)
    coll.document(doc_id).set(data, merge=True)


def _get_user(uid: str) -> Dict[str, Any]:
    user = db._memory_users.get(uid)
    if user is None:
        user = {"uid": uid, "name": "Student", "email": f"{uid}@placeholder.local", "role": "student",
                "is_premium": False, "credit_balance": 0.0, "access_type": "free"}
        db._memory_users[uid] = user
    return user


def _is_eligible_for_referral(user: Dict[str, Any]) -> bool:
    return user.get("access_type") in ("paid", "offline-comp") or user.get("is_premium") is True


def generate_referral_code() -> str:
    return CODE_PREFIX + "".join(random.choice(CODE_ALPHABET) for _ in range(4))


def _code_is_unique(code: str) -> bool:
    existing = _query("referrals", "code", "==", code)
    return len(existing) == 0


def _generate_unique_code(retries: int = 10) -> str:
    for _ in range(retries):
        code = generate_referral_code()
        if _code_is_unique(code):
            return code
    raise RuntimeError("Could not generate a unique referral code after retries.")


async def ensure_referral_code(uid: str) -> Dict[str, Any]:
    user = _get_user(uid)
    if not _is_eligible_for_referral(user):
        return {
            "eligible": False,
            "reason": "Refer & Earn unlocks only after a successful first payment (or an offline coupon activation).",
            "code": None,
        }

    existing = _query("referrals", "uid", "==", uid)
    if existing:
        ref = existing[0]
        return {"eligible": True, "code": ref["code"], "created_at": ref.get("created_at")}

    code = _generate_unique_code()
    _save("referrals", code, {
        "code": code,
        "uid": uid,
        "created_at": _now(),
        "active": True,
    })
    await db.update_user(uid, {"referral_code": code})
    return {"eligible": True, "code": code, "created_at": _now()}


async def get_referral_status(uid: str) -> Dict[str, Any]:
    user = _get_user(uid)
    code = user.get("referral_code")
    redemptions = _query("referral_redemptions", "referrer_uid", "==", uid)
    rewarded = [r for r in redemptions if r.get("status") == "rewarded"]
    pending = [r for r in redemptions if r.get("status") == "pending"]
    return {
        "code": code,
        "eligible": _is_eligible_for_referral(user),
        "rewards_total": REFERRAL_REWARD_INR,
        "cap": REFERRAL_REWARD_CAP,
        "rewarded_count": len(rewarded),
        "pending_count": len(pending),
        "remaining_count": max(0, REFERRAL_REWARD_CAP - len(rewarded)),
        "credit_balance_inr": _round2(user.get("credit_balance", 0.0)),
        "history": [
            {
                "referee_uid": r.get("referee_uid"),
                "referee_email": r.get("referee_email"),
                "status": r.get("status"),
                "reward_amount": r.get("reward_amount", REFERRAL_REWARD_INR),
                "created_at": r.get("created_at"),
                "rewarded_at": r.get("rewarded_at"),
            }
            for r in sorted(redemptions + pending, key=lambda x: x.get("created_at") or "")
        ],
    }


async def attribute_referral_on_signup(referee_uid: str, code: str, device_id: str) -> Optional[Dict[str, Any]]:
    if not code:
        return None
    refs = _query("referrals", "code", "==", code)
    if not refs:
        return None
    referral = refs[0]
    referrer_uid = referral.get("uid")
    if not referrer_uid or referrer_uid == referee_uid:
        return None

    referrer = _get_user(referrer_uid)
    active_device_id = (referrer.get("active_device") or {}).get("device_id")
    flagged = bool(device_id and active_device_id and active_device_id == device_id)

    if flagged:
        from app.services.device_service import log_device_event
        await log_device_event(
            referee_uid, "self_referral_blocked", device_id or "unknown", None, "", flagged=True,
        )
        return None

    _save("referral_redemptions", f"{referrer_uid}_{referee_uid}", {
        "code": code,
        "referrer_uid": referrer_uid,
        "referee_uid": referee_uid,
        "referee_email": _get_user(referee_uid).get("email"),
        "status": "pending",
        "reward_amount": REFERRAL_REWARD_INR,
        "created_at": _now(),
    })
    await db.update_user(referee_uid, {
        "referred_by": referrer_uid,
        "referral_code_used": code,
    })
    return {"referrer_uid": referrer_uid, "code": code}


async def get_share_link(uid: str) -> Dict[str, Any]:
    user = _get_user(uid)
    eligible = _is_eligible_for_referral(user)
    shares = _query("shares", "uid", "==", uid)
    rewarded = [s for s in shares if s.get("status") == "rewarded"]
    return {
        "eligible": eligible,
        "cap": SHARE_REWARD_CAP,
        "rewarded_count": len(rewarded),
        "remaining_count": max(0, SHARE_REWARD_CAP - len(rewarded)),
        "share_url_template": f"{SHARE_LINK_HOST}/s/{{token}}",
        "shares": [
            {
                "token": s.get("token"),
                "share_url": f"{SHARE_LINK_HOST}/s/{s.get('token')}",
                "status": s.get("status"),
                "clicks": len(s.get("clicks") or []),
                "created_at": s.get("created_at"),
                "converted_at": s.get("converted_at"),
                "converted_uid": s.get("converted_uid"),
            }
            for s in sorted(shares, key=lambda x: x.get("created_at") or "", reverse=True)
        ],
    }


async def create_share_token(uid: str) -> Dict[str, Any]:
    user = _get_user(uid)
    if not _is_eligible_for_referral(user):
        return {"eligible": False, "reason": "Share-to-earn unlocks only after a successful first payment."}
    shares = _query("shares", "uid", "==", uid)
    rewarded = [s for s in shares if s.get("status") == "rewarded"]
    if len(rewarded) >= SHARE_REWARD_CAP:
        return {
            "eligible": True,
            "cap_reached": True,
            "message": f"You have already reached the lifetime cap of {SHARE_REWARD_CAP} rewarded shares.",
        }
    token = secrets.token_urlsafe(8).replace("_", "A").replace("-", "B")[:10]
    _save("shares", token, {
        "token": token,
        "uid": uid,
        "created_at": _now(),
        "clicks": [],
        "status": "active",
    })
    return {
        "eligible": True,
        "token": token,
        "share_url": f"{SHARE_LINK_HOST}/s/{token}",
    }


async def track_share_click(token: str, ua: str = "") -> Optional[Dict[str, Any]]:
    shares = _query("shares", "token", "==", token)
    if not shares:
        return None
    share = shares[0]
    clicks = share.get("clicks") or []
    clicks.append({"ts": _now(), "ua": ua[:200]})
    _save("shares", token, {"clicks": clicks})
    return {"sharer_uid": share.get("uid"), "clicks": len(clicks)}


async def attribute_share_on_signup(referee_uid: str, token: str) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    shares = _query("shares", "token", "==", token)
    if not shares:
        return None
    share = shares[0]
    sharer_uid = share.get("uid")
    if not sharer_uid or sharer_uid == referee_uid:
        return None
    if share.get("status") == "rewarded":
        return None
    if share.get("converted_uid"):
        return None
    _save("shares", token, {
        "converted_uid": referee_uid,
        "converted_at": _now(),
        "status": "converted",
    })
    await db.update_user(referee_uid, {
        "share_attribution": {"token": token, "sharer_uid": sharer_uid},
    })
    return {"sharer_uid": sharer_uid, "token": token}


async def get_active_coupon() -> Dict[str, Any]:
    if db.use_live_firestore:
        snap = db.collection("coupon_codes").document(COUPON_DOC_ID).get()
        if not snap.exists:
            return {"code": "", "updated_at": None, "updated_by": None}
        c = snap.to_dict() or {}
    else:
        c = (db.collection("coupon_codes").document(COUPON_DOC_ID).get().to_dict()) if COUPON_DOC_ID in db._memory_collections.get("coupon_codes", {}) else {}
    if not c:
        return {"code": "", "updated_at": None, "updated_by": None}
    return {"code": c.get("code", ""), "updated_at": c.get("updated_at"), "updated_by": c.get("updated_by")}


async def set_active_coupon(code: str, admin_uid: str) -> Dict[str, Any]:
    code = (code or "").strip()
    _save("coupon_codes", COUPON_DOC_ID, {
        "code": code,
        "updated_at": _now(),
        "updated_by": admin_uid,
    })
    return {"code": code, "updated_at": _now(), "updated_by": admin_uid}


async def redeem_coupon(uid: str, code: str) -> Dict[str, Any]:
    code = (code or "").strip()
    active = await get_active_coupon()
    if not active.get("code") or code.lower() != active["code"].lower():
        return {"success": False, "reason": "Invalid coupon code."}
    user = _get_user(uid)
    if user.get("access_type") == "offline-comp":
        return {"success": True, "already_active": True, "message": "Coupon already applied to this account."}
    await db.update_user(uid, {
        "access_type": "offline-comp",
        "is_premium": True,
        "coupon_redeemed_at": _now(),
    })
    _save("coupon_redemptions", uid, {
        "uid": uid,
        "code": code,
        "redeemed_at": _now(),
        "marked_by": "self",
    })
    return {"success": True, "already_active": False, "message": "Free offline access activated."}


def _credit_ledger(uid: str, source: str, amount: float, ref: str = "") -> None:
    _save("credit_ledger", f"{uid}_{int(datetime.now(timezone.utc).timestamp() * 1000)}_{secrets.token_hex(3)}", {
        "uid": uid,
        "source": source,
        "amount": _round2(amount),
        "ref": ref,
        "created_at": _now(),
    })


def credit_balance_of(uid: str) -> float:
    return _round2(_get_user(uid).get("credit_balance", 0.0))


async def grant_credit(uid: str, amount: float, source: str, ref: str = "") -> float:
    user = _get_user(uid)
    new_balance = _round2(user.get("credit_balance", 0.0) + amount)
    await db.update_user(uid, {"credit_balance": new_balance})
    _credit_ledger(uid, source, amount, ref)
    return new_balance


async def consume_credit(uid: str, amount: float, ref: str = "") -> float:
    user = _get_user(uid)
    current = _round2(user.get("credit_balance", 0.0))
    consumed = _round2(min(current, max(0.0, amount)))
    new_balance = _round2(current - consumed)
    await db.update_user(uid, {"credit_balance": new_balance})
    if consumed > 0:
        _credit_ledger(uid, "consumption", -consumed, ref)
    return consumed


async def award_referral_and_share_rewards(referee_uid: str, order_id: str) -> Dict[str, Any]:
    """
    Called when a referee's payment is verified. Applies referral reward (capped)
    and share reward (capped). Idempotent via per-redemption status flags.
    """
    result: Dict[str, Any] = {"referral_awarded": False, "share_awarded": False}
    user = _get_user(referee_uid)

    referrer_uid = user.get("referred_by")
    if referrer_uid:
        redemptions = _query("referral_redemptions", "referee_uid", "==", referee_uid)
        for r in redemptions:
            if r.get("status") == "pending":
                referrer_redemptions = _query("referral_redemptions", "referrer_uid", "==", referrer_uid)
                already_rewarded = [x for x in referrer_redemptions if x.get("status") == "rewarded"]
                if len(already_rewarded) >= REFERRAL_REWARD_CAP:
                    break
                await grant_credit(referrer_uid, REFERRAL_REWARD_INR, "referral_reward",
                                   ref=f"{order_id}:{referee_uid}")
                _save("referral_redemptions", r["id"], {
                    "status": "rewarded",
                    "rewarded_at": _now(),
                    "order_id": order_id,
                })
                result["referral_awarded"] = True
                result["referrer_uid"] = referrer_uid
                break

    share_attr = user.get("share_attribution") or {}
    share_token = share_attr.get("token")
    sharer_uid = share_attr.get("sharer_uid")
    if share_token and sharer_uid:
        shares = _query("shares", "token", "==", share_token)
        if shares:
            share = shares[0]
            if share.get("status") != "rewarded":
                sharer_shares = _query("shares", "uid", "==", sharer_uid)
                already_rewarded = [s for s in sharer_shares if s.get("status") == "rewarded"]
                if len(already_rewarded) < SHARE_REWARD_CAP:
                    await grant_credit(sharer_uid, SHARE_REWARD_INR, "share_reward",
                                       ref=f"{order_id}:{referee_uid}")
                    _save("shares", share_token, {
                        "status": "rewarded",
                        "rewarded_at": _now(),
                        "reward_order_id": order_id,
                    })
                    result["share_awarded"] = True
                    result["sharer_uid"] = sharer_uid

    return result


async def credits_ledger(uid: str, limit: int = 50) -> List[Dict[str, Any]]:
    items = _query("credit_ledger", "uid", "==", uid)
    items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return items[:limit]


async def apply_payment_success(uid: str, order_id: str, credit_applied: float) -> Dict[str, Any]:
    user = _get_user(uid)
    now = datetime.now(timezone.utc)
    current_expiry = user.get("subscription_expires_at")
    base_expiry = now
    if current_expiry:
        try:
            parsed = datetime.fromisoformat(current_expiry.replace("Z", "+00:00"))
            if parsed > now:
                base_expiry = parsed
        except Exception:
            pass
    new_expiry = base_expiry.replace(microsecond=0)
    from datetime import timedelta
    new_expiry = (new_expiry + timedelta(days=30)).isoformat()

    updates: Dict[str, Any] = {
        "is_premium": True,
        "subscription_expires_at": new_expiry,
    }
    if user.get("access_type") != "offline-comp":
        updates["access_type"] = "paid"

    if credit_applied > 0:
        await consume_credit(uid, credit_applied, ref=order_id)

    await db.update_user(uid, updates)
    rewards = await award_referral_and_share_rewards(uid, order_id)
    return {
        "subscription_expires_at": new_expiry,
        "is_premium": True,
        "credit_consumed": credit_applied,
        "credit_balance_inr": credit_balance_of(uid),
        "rewards": rewards,
    }


def user_quote(uid: str) -> Dict[str, Any]:
    user = _get_user(uid)
    balance = _round2(user.get("credit_balance", 0.0))
    return {**compute_quote(balance), "credit_balance_inr": balance}


async def grant_referrer_credit_for_payment(uid: str, payment_id: str) -> Dict[str, Any]:
    user = _get_user(uid)
    referrer_uid = user.get("referred_by")
    if not referrer_uid:
        return {"awarded": False, "reason": "no_referrer"}
    redemptions = _query("referral_redemptions", "referee_uid", "==", uid)
    for r in redemptions:
        if r.get("status") != "pending":
            continue
        referrer_redemptions = _query("referral_redemptions", "referrer_uid", "==", referrer_uid)
        already = [x for x in referrer_redemptions if x.get("status") == "rewarded"]
        if len(already) >= REFERRAL_REWARD_CAP:
            return {"awarded": False, "reason": "referrer_cap_reached"}
        await grant_credit(referrer_uid, REFERRAL_REWARD_INR, "referral_reward", ref=payment_id)
        _save("referral_redemptions", r["id"], {
            "status": "rewarded",
            "rewarded_at": _now(),
            "payment_id": payment_id,
        })
        return {"awarded": True, "referrer_uid": referrer_uid, "amount": REFERRAL_REWARD_INR}
    return {"awarded": False, "reason": "no_pending_redemption"}


async def convert_share_if_eligible(uid: str) -> Dict[str, Any]:
    user = _get_user(uid)
    attr = user.get("share_attribution") or {}
    token = attr.get("token")
    if not token:
        return {"converted": False, "reason": "no_share_attribution"}
    shares = _query("shares", "token", "==", token)
    if not shares:
        return {"converted": False, "reason": "share_not_found"}
    share = shares[0]
    if share.get("status") == "rewarded":
        return {"converted": False, "reason": "already_rewarded"}
    sharer_uid = share.get("uid")
    sharer_shares = _query("shares", "uid", "==", sharer_uid)
    already = [s for s in sharer_shares if s.get("status") == "rewarded"]
    if len(already) >= SHARE_REWARD_CAP:
        return {"converted": False, "reason": "sharer_cap_reached"}
    await grant_credit(sharer_uid, SHARE_REWARD_INR, "share_reward", ref=uid)
    _save("shares", token, {
        "status": "rewarded",
        "rewarded_at": _now(),
        "rewarded_referee_uid": uid,
    })
    return {"converted": True, "sharer_uid": sharer_uid, "amount": SHARE_REWARD_INR}


async def admin_growth_overview() -> Dict[str, Any]:
    referrals = _query("referrals", "active", "==", True)
    shares = _query("shares", "status", "in", ["rewarded", "converted", "active"])
    redemptions = _query("referral_redemptions", "status", "==", "rewarded")
    return {
        "total_referral_codes": len(referrals),
        "total_shares": len(shares),
        "total_referral_rewards_paid": len(redemptions) * REFERRAL_REWARD_INR,
        "total_share_rewards_paid": len([s for s in shares if s.get("status") == "rewarded"]) * SHARE_REWARD_INR,
        "referral_reward_cap": REFERRAL_REWARD_CAP,
        "share_reward_cap": SHARE_REWARD_CAP,
        "referral_reward_inr": REFERRAL_REWARD_INR,
        "share_reward_inr": SHARE_REWARD_INR,
    }


async def admin_list_referrals() -> List[Dict[str, Any]]:
    items = _query("referrals", "active", "==", True)
    out = []
    for r in items:
        uid = r.get("uid")
        user = _get_user(uid) if uid else {}
        redemptions = _query("referral_redemptions", "referrer_uid", "==", uid)
        rewarded = [x for x in redemptions if x.get("status") == "rewarded"]
        out.append({
            "code": r.get("code"),
            "uid": uid,
            "user_name": user.get("name"),
            "user_email": user.get("email"),
            "created_at": r.get("created_at"),
            "rewarded_count": len(rewarded),
            "pending_count": len([x for x in redemptions if x.get("status") == "pending"]),
        })
    return out


async def admin_list_shares() -> List[Dict[str, Any]]:
    items = _query("shares", "uid", "!=", None)
    out = []
    for s in items:
        uid = s.get("uid")
        user = _get_user(uid) if uid else {}
        out.append({
            "token": s.get("token"),
            "uid": uid,
            "user_name": user.get("name"),
            "user_email": user.get("email"),
            "status": s.get("status"),
            "clicks": len(s.get("clicks") or []),
            "created_at": s.get("created_at"),
            "rewarded_at": s.get("rewarded_at"),
        })
    return out


async def admin_list_devices() -> List[Dict[str, Any]]:
    items = _query("device_bindings", "uid", "!=", None)
    out = []
    seen = set()
    for it in items:
        uid = it.get("uid")
        if uid in seen:
            continue
        seen.add(uid)
        user = _get_user(uid)
        active = user.get("active_device") or {}
        out.append({
            "uid": uid,
            "user_name": user.get("name"),
            "user_email": user.get("email"),
            "active_device_id": active.get("device_id"),
            "active_device_name": active.get("device_name"),
            "active_platform": active.get("platform"),
            "bound_at": active.get("bound_at"),
            "last_seen_at": active.get("last_seen_at"),
        })
    return out


async def admin_list_device_events() -> List[Dict[str, Any]]:
    items = _query("device_events", "uid", "!=", None)
    items.sort(key=lambda x: x.get("ts") or "", reverse=True)
    return items[:200]
