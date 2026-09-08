import time
import pytest
from starlette.testclient import TestClient

from app.main import app
from app.core.config import settings
from app.core.security import create_access_token, compute_hmac_signature
from app.services.pricing import compute_quote, REFERRAL_REWARD_INR, SHARE_REWARD_INR
from app.services import growth_service as gs

client = TestClient(app)


def hm(path, method="GET"):
    ts = str(time.time())
    return {
        "x-api-key-id": settings.API_KEY_ID,
        "x-api-key-secret": settings.API_KEY_SECRET,
        "x-timestamp": ts,
        "x-hmac": compute_hmac_signature(settings.API_KEY_SECRET, ts, method, path),
    }


def _uid() -> str:
    import uuid
    return f"usr_test_{uuid.uuid4().hex[:10]}"


def auth(uid, email="t@vastavik.com", name="Tester", role="student"):
    return create_access_token({"sub": uid, "email": email, "role": role, "name": name})


def test_pricing_math_full_price():
    q = compute_quote(0.0)
    assert q["base_amount"] == 149.0
    assert q["discount_amount"] == 0.0
    assert q["taxable_amount"] == 149.0
    assert q["gst_amount"] == 26.82
    assert q["total_amount"] == 175.82
    assert q["total_amount_paise"] == 17582


def test_pricing_math_with_credit_25():
    q = compute_quote(REFERRAL_REWARD_INR)
    assert q["discount_amount"] == 25.0
    assert q["taxable_amount"] == 124.0
    assert q["gst_amount"] == 22.32
    assert q["total_amount"] == 146.32
    assert q["total_amount_paise"] == 14632


def test_pricing_math_with_credit_35():
    q = compute_quote(35.0)
    assert q["discount_amount"] == 35.0
    assert q["taxable_amount"] == 114.0
    assert q["gst_amount"] == 20.52
    assert q["total_amount"] == 134.52


def test_pricing_math_credit_exceeds_base():
    q = compute_quote(500.0)
    assert q["discount_amount"] == 149.0
    assert q["taxable_amount"] == 0.0
    assert q["gst_amount"] == 0.0
    assert q["total_amount"] == 0.0
    assert q["total_amount_paise"] == 0


def test_pricing_math_partial_share():
    q = compute_quote(SHARE_REWARD_INR)
    assert q["discount_amount"] == 10.0
    assert q["taxable_amount"] == 139.0
    assert q["gst_amount"] == 25.02
    assert q["total_amount"] == 164.02


def test_referral_code_uniqueness():
    a = gs.generate_referral_code()
    b = gs.generate_referral_code()
    assert a.startswith("VK")
    assert len(a) == 6
    assert a != b


def test_referral_gated_before_payment():
    import asyncio
    uid = _uid()
    async def setup():
        db_user = await gs._get_user(uid)
        db_user["access_type"] = "free"
        db_user["is_premium"] = False
        return await gs.ensure_referral_code(uid)
    res = asyncio.run(setup())
    assert res["eligible"] is False
    assert res["code"] is None


def test_referral_full_flow():
    import asyncio

    async def run():
        referrer = _uid()
        referee = _uid()
        r = await gs._get_user(referrer)
        r["access_type"] = "paid"
        r["is_premium"] = True
        rr = await gs._get_user(referee)
        rr["access_type"] = "free"

        code_info = await gs.ensure_referral_code(referrer)
        assert code_info["eligible"] is True
        code = code_info["code"]

        stat_before = await gs.get_referral_status(referrer)
        assert stat_before["rewarded_count"] == 0

        await gs.attribute_referral_on_signup(referee, code, device_id="dev_A")
        rr["access_type"] = "paid"
        rr["is_premium"] = True

        order_id = f"order_{_uid()}"
        result = await gs.award_referral_and_share_rewards(referee, order_id)
        assert result["referral_awarded"] is True
        assert result["referrer_uid"] == referrer

        stat = await gs.get_referral_status(referrer)
        assert stat["rewarded_count"] == 1
        assert await gs.credit_balance_of(referrer) == REFERRAL_REWARD_INR

        result2 = await gs.award_referral_and_share_rewards(referee, order_id)
        assert result2["referral_awarded"] is False

    asyncio.run(run())


def test_referral_cap_three():
    import asyncio

    async def run():
        referrer = _uid()
        r = await gs._get_user(referrer)
        r["access_type"] = "paid"
        r["is_premium"] = True
        code = (await gs.ensure_referral_code(referrer))["code"]

        for i in range(3):
            referee = _uid()
            rr = await gs._get_user(referee)
            rr["access_type"] = "paid"
            rr["is_premium"] = True
            await gs.attribute_referral_on_signup(referee, code, device_id=f"dev_{i}")
            await gs.award_referral_and_share_rewards(referee, f"order_{i}")

        fourth_referee = _uid()
        rr4 = await gs._get_user(fourth_referee)
        rr4["access_type"] = "paid"
        rr4["is_premium"] = True
        await gs.attribute_referral_on_signup(fourth_referee, code, device_id="dev_4")
        result = await gs.award_referral_and_share_rewards(fourth_referee, "order_4")
        assert result["referral_awarded"] is False

        stat = await gs.get_referral_status(referrer)
        assert stat["rewarded_count"] == 3
        assert stat["remaining_count"] == 0
        assert await gs.credit_balance_of(referrer) == 3 * REFERRAL_REWARD_INR

    asyncio.run(run())


def test_share_full_flow():
    import asyncio

    async def run():
        sharer = _uid()
        s = await gs._get_user(sharer)
        s["access_type"] = "paid"
        s["is_premium"] = True
        link = await gs.create_share_token(sharer)
        assert link.get("eligible") is True
        token = link["token"]

        await gs.track_share_click(token, ua="Mozilla/5.0")
        await gs.track_share_click(token, ua="Mozilla/5.0")

        referee = _uid()
        rr = await gs._get_user(referee)
        rr["access_type"] = "paid"
        rr["is_premium"] = True
        await gs.attribute_share_on_signup(referee, token)
        result = await gs.convert_share_if_eligible(referee)
        assert result["converted"] is True
        assert await gs.credit_balance_of(sharer) == SHARE_REWARD_INR

    asyncio.run(run())


def test_share_cap_two():
    import asyncio

    async def run():
        sharer = _uid()
        s = await gs._get_user(sharer)
        s["access_type"] = "paid"
        s["is_premium"] = True
        tokens = []
        for _ in range(3):
            link = await gs.create_share_token(sharer)
            tokens.append(link["token"])

        for idx, t in enumerate(tokens[:2]):
            ref = _uid()
            rr = await gs._get_user(ref)
            rr["access_type"] = "paid"
            rr["is_premium"] = True
            await gs.attribute_share_on_signup(ref, t)
            r = await gs.convert_share_if_eligible(ref)
            assert r["converted"] is True, r

        ref3 = _uid()
        rr3 = await gs._get_user(ref3)
        rr3["access_type"] = "paid"
        rr3["is_premium"] = True
        await gs.attribute_share_on_signup(ref3, tokens[2])
        r = await gs.convert_share_if_eligible(ref3)
        assert r["converted"] is False
        assert await gs.credit_balance_of(sharer) == 2 * SHARE_REWARD_INR

    asyncio.run(run())


def test_coupon_redemption_marks_offline_comp():
    import asyncio

    async def run():
        admin = _uid()
        await gs.set_active_coupon("VASTAVIKOFFLINE2025", admin)

        student = _uid()
        st = await gs._get_user(student)
        st["access_type"] = "free"
        st["is_premium"] = False

        result = await gs.redeem_coupon(student, "vastavikoffline2025")
        assert result["success"] is True
        st = await gs._get_user(student)
        assert st["access_type"] == "offline-comp"
        assert st["is_premium"] is True

    asyncio.run(run())


def test_coupon_invalid():
    import asyncio

    async def run():
        student = _uid()
        u = await gs._get_user(student)
        u["access_type"] = "free"
        result = await gs.redeem_coupon(student, "WRONGCODE")
        assert result["success"] is False

    asyncio.run(run())


def test_pricing_quote_endpoint():
    import asyncio
    uid = _uid()
    async def setup():
        u = await gs._get_user(uid)
        u["credit_balance"] = 50.0
    asyncio.run(setup())
    token = auth(uid)
    h = hm("/api/v1/pricing/quote", "GET")
    h["Authorization"] = f"Bearer {token}"
    r = client.get("/api/v1/pricing/quote", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert data["discount_amount"] == 50.0
    assert data["taxable_amount"] == 99.0
    assert data["gst_amount"] == 17.82
    assert data["total_amount"] == 116.82


def test_razorpay_signature_unit():
    from app.services import razorpay_service as rzp
    settings.RAZORPAY_KEY_SECRET = "test_secret"
    assert rzp.verify_payment_signature("order_123", "pay_456", rzp._compute_for_test("order_123", "pay_456", "test_secret")) is True
    assert rzp.verify_payment_signature("order_123", "pay_456", "bad") is False
    settings.RAZORPAY_KEY_SECRET = None
