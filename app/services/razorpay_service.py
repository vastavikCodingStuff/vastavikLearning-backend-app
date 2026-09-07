import base64
import hashlib
import hmac
import uuid
from typing import Any, Dict, Optional

import httpx

from app.core.config import settings

RAZORPAY_API_BASE = "https://api.razorpay.com/v1"


def is_configured() -> bool:
    return bool(settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET)


def _auth_header() -> str:
    raw = f"{settings.RAZORPAY_KEY_ID}:{settings.RAZORPAY_KEY_SECRET}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("utf-8")


async def create_order(amount_paise: int, receipt: str, notes: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """
    Creates a Razorpay order via the Orders API. Raises RuntimeError when the
    gateway is not configured or the API call fails so callers can fall back.
    """
    if not is_configured():
        raise RuntimeError("Razorpay is not configured (RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET missing).")

    payload = {
        "amount": amount_paise,
        "currency": "INR",
        "receipt": receipt,
        "notes": notes or {},
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{RAZORPAY_API_BASE}/orders",
            json=payload,
            headers={"Authorization": _auth_header(), "Content-Type": "application/json"},
        )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Razorpay order creation failed: HTTP {resp.status_code} {resp.text[:200]}")
    return resp.json()


def verify_payment_signature(razorpay_order_id: str, razorpay_payment_id: str, razorpay_signature: str) -> bool:
    """
    Razorpay standard handshake verification:
      expected = HMAC_SHA256(key_secret, f"{order_id}|{payment_id}")
    """
    if not settings.RAZORPAY_KEY_SECRET:
        return False
    message = f"{razorpay_order_id}|{razorpay_payment_id}".encode("utf-8")
    expected = hmac.new(
        settings.RAZORPAY_KEY_SECRET.encode("utf-8"), message, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, razorpay_signature)


def verify_webhook_signature(raw_body: bytes, signature: str) -> bool:
    """
    Razorpay webhook verification:
      expected = HMAC_SHA256(webhook_secret, raw_request_body)
    """
    if not settings.RAZORPAY_WEBHOOK_SECRET or not signature:
        return False
    expected = hmac.new(
        settings.RAZORPAY_WEBHOOK_SECRET.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def generate_local_order_id() -> str:
    """Fallback order id used when Razorpay keys are absent (dev/test mode)."""
    return f"order_local_{uuid.uuid4().hex[:14]}"


def _compute_for_test(order_id: str, payment_id: str, secret: str) -> str:
    message = f"{order_id}|{payment_id}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
