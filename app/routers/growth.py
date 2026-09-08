from typing import Any, Dict
from fastapi import APIRouter, HTTPException, Depends

from app.core.security import get_current_user, require_admin_user
from app.services import growth_service as gs
from app.services.device_service import unlink_device

router = APIRouter(prefix="/api/v1", tags=["Growth - Referrals, Shares, Coupons"])


@router.post("/referral/generate")
async def generate_code(current_user: Dict[str, Any] = Depends(get_current_user)):
    uid = current_user.get("sub")
    result = await gs.ensure_referral_code(uid)
    if not result.get("eligible"):
        raise HTTPException(status_code=403, detail=result.get("reason", "Not eligible."))
    return result


@router.get("/referral/status")
async def referral_status(current_user: Dict[str, Any] = Depends(get_current_user)):
    uid = current_user.get("sub")
    return await gs.get_referral_status(uid)


@router.post("/share/generate")
async def generate_share(current_user: Dict[str, Any] = Depends(get_current_user)):
    uid = current_user.get("sub")
    result = await gs.create_share_token(uid)
    if not result.get("eligible"):
        raise HTTPException(status_code=403, detail=result.get("reason", "Not eligible."))
    return result


@router.get("/share/status")
async def share_status(current_user: Dict[str, Any] = Depends(get_current_user)):
    uid = current_user.get("sub")
    return await gs.get_share_link(uid)


@router.post("/share/track/{token}")
async def track_share(token: str, request_payload: Dict[str, str] = None):
    ua = (request_payload or {}).get("user_agent", "")
    result = await gs.track_share_click(token, ua)
    if not result:
        raise HTTPException(status_code=404, detail="Share token not found.")
    return result


@router.post("/coupon/redeem")
async def redeem_coupon(payload: Dict[str, str], current_user: Dict[str, Any] = Depends(get_current_user)):
    uid = current_user.get("sub")
    code = (payload.get("code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="Coupon code is required.")
    result = await gs.redeem_coupon(uid, code)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("reason", "Invalid coupon."))
    return result


@router.get("/pricing/quote")
async def pricing_quote(current_user: Dict[str, Any] = Depends(get_current_user)):
    return gs.user_quote(current_user.get("sub"))


@router.get("/credits/balance")
async def credits_balance(current_user: Dict[str, Any] = Depends(get_current_user)):
    uid = current_user.get("sub")
    return {
        "credit_balance_inr": gs.credit_balance_of(uid),
        "ledger": await gs.credits_ledger(uid),
    }


admin_router = APIRouter(prefix="/admin", tags=["Admin - Growth"])


@admin_router.get("/growth/overview", dependencies=[Depends(require_admin_user)])
async def growth_overview():
    return await gs.admin_growth_overview()


@admin_router.get("/growth/referrals", dependencies=[Depends(require_admin_user)])
async def list_referrals():
    return await gs.admin_list_referrals()


@admin_router.get("/growth/shares", dependencies=[Depends(require_admin_user)])
async def list_shares():
    return await gs.admin_list_shares()


@admin_router.get("/growth/coupon")
async def get_coupon(_admin: Dict[str, Any] = Depends(require_admin_user)):
    return await gs.get_active_coupon()


@admin_router.put("/growth/coupon")
async def set_coupon(payload: Dict[str, str], admin: Dict[str, Any] = Depends(require_admin_user)):
    code = (payload.get("code") or "").strip()
    return await gs.set_active_coupon(code, admin.get("sub", "admin"))


@admin_router.get("/growth/devices")
async def list_devices(_admin: Dict[str, Any] = Depends(require_admin_user)):
    return await gs.admin_list_devices()


@admin_router.get("/growth/device-events")
async def list_device_events(_admin: Dict[str, Any] = Depends(require_admin_user)):
    return await gs.admin_list_device_events()


@admin_router.post("/growth/devices/{uid}/unlink", dependencies=[Depends(require_admin_user)])
async def admin_unlink(uid: str):
    new_tv = await unlink_device(uid)
    return {"success": True, "token_version": new_tv}
