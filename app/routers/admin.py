from typing import Dict, Any
from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel

from app.core.security import require_admin_user
from app.core.circuit_breaker import route_manager

router = APIRouter(prefix="/admin", tags=["Dynamic Admin & Circuit Breaker Control"])


class RouteStatusUpdateRequest(BaseModel):
    enabled: bool


@router.get("/routes", response_model=Dict[str, bool])
async def get_all_routes_status(admin_user: Dict[str, Any] = Depends(require_admin_user)):
    """
    Returns the real-time operational status of all backend route groups.
    """
    return await route_manager.get_all()


@router.post("/routes/{feature}/toggle", response_model=Dict[str, Any])
async def toggle_route(feature: str, admin_user: Dict[str, Any] = Depends(require_admin_user)):
    """
    Toggles a specific route/feature (e.g. 'ai_chat', 'code_execution') on or off instantly
    without requiring a binary rebuild or process restart.
    """
    new_status = await route_manager.toggle(feature)
    return {
        "success": True,
        "feature": feature,
        "enabled": new_status,
        "status_description": "ONLINE" if new_status else "MAINTENANCE (503 Fast-Fail Active)",
    }


@router.post("/routes/{feature}/set", response_model=Dict[str, Any])
async def set_route_status(
    feature: str,
    request: RouteStatusUpdateRequest,
    admin_user: Dict[str, Any] = Depends(require_admin_user)
):
    """
    Sets a feature's operational status explicitly.
    """
    status_val = await route_manager.set_status(feature, request.enabled)
    return {
        "success": True,
        "feature": feature,
        "enabled": status_val,
        "status_description": "ONLINE" if status_val else "MAINTENANCE (503 Fast-Fail Active)",
    }
