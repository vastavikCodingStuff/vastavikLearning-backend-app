from typing import Dict, Any

BASE_PRICE_INR: float = 149.0
GST_RATE: float = 0.18
CURRENCY: str = "INR"
SUBSCRIPTION_DAYS: int = 30

REFERRAL_REWARD_INR: float = 25.0
REFERRAL_REWARD_CAP: int = 3
SHARE_REWARD_INR: float = 10.0
SHARE_REWARD_CAP: int = 2

PLAN_ID: str = "monthly_pro"
PLAN_NAME: str = "Vastavik Pro Monthly"


def _round2(value: float) -> float:
    return round(value + 1e-9, 2)


def compute_quote(credit_balance: float = 0.0) -> Dict[str, Any]:
    """
    Pricing math:
      - Base subscription: Rs.149 + 18% GST.
      - Credits (referral Rs.25 / share Rs.10) are applied to the base
        price BEFORE GST, then GST is calculated on the discounted base.
      - Total never goes below Rs.0.
    """
    base = BASE_PRICE_INR
    discount = _round2(max(0.0, min(credit_balance, base)))
    taxable = _round2(base - discount)
    gst = _round2(taxable * GST_RATE)
    total = _round2(taxable + gst)
    return {
        "plan_id": PLAN_ID,
        "plan_name": PLAN_NAME,
        "base_amount": _round2(base),
        "discount_amount": discount,
        "taxable_amount": taxable,
        "gst_rate": GST_RATE,
        "gst_amount": gst,
        "total_amount": total,
        "total_amount_paise": int(round(total * 100)),
        "currency": CURRENCY,
        "subscription_days": SUBSCRIPTION_DAYS,
    }


def to_paise(amount_inr: float) -> int:
    return int(round(amount_inr * 100))


def from_paise(amount_paise: int) -> float:
    return _round2(amount_paise / 100.0)
