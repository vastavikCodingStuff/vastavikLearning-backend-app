import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.rate_limiter import rate_limit
from app.core.security import get_current_user_optional, get_current_user
from app.db.firebase import db

logger = logging.getLogger("vastavik.activity")
router = APIRouter(prefix="/api/v1/activity", tags=["Activity Log"])


@router.post("/log", dependencies=[Depends(rate_limit("general"))])
async def ingest_log(
    request: Request,
    current_user: Optional[Dict[str, Any]] = Depends(get_current_user_optional)
) -> Dict[str, Any]:
    """
    Accept a JSON array of activity log entries (or a single object) and persist
    them to Firestore activity_logs.
    """
    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid JSON body: {e}",
        )

    if isinstance(body, dict):
        entries: List[Dict[str, Any]] = [body]
    elif isinstance(body, list):
        entries = body
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Activity body must be a JSON object or array of objects.",
        )

    user_uid = (current_user.get("sub") or current_user.get("uid")) if current_user else None
    user_name = current_user.get("name") if current_user else None
    user_email = current_user.get("email") if current_user else None
    is_admin = (current_user.get("role") == "admin") if current_user else False

    accepted = 0
    rejected = 0
    rejected_reasons: List[str] = []

    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            rejected += 1
            rejected_reasons.append(f"#{idx}: not an object")
            continue
        if "event" not in entry:
            rejected += 1
            rejected_reasons.append(f"#{idx}: missing event")
            continue

        if not entry.get("id"):
            entry["id"] = f"act_{uuid.uuid4().hex[:12]}"

        # Prevent spoofing of another user's identity if authenticated
        if user_uid:
            if not is_admin or not entry.get("uid"):
                entry["uid"] = user_uid
        if user_name and (not is_admin or not entry.get("student_name")):
            entry["student_name"] = user_name
        if user_email and (not is_admin or not entry.get("student_email")):
            entry["student_email"] = user_email

        entry.setdefault("received_at", datetime.now(timezone.utc).isoformat())
        entry.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        try:
            await db.save_activity_log(entry)
            accepted += 1
        except Exception as e:  # pragma: no cover
            logger.warning("activity save failed for %s: %s", entry.get("id"), e)
            rejected += 1
            rejected_reasons.append(f"#{idx}: {e}")

    return {
        "success": True,
        "accepted": accepted,
        "rejected": rejected,
        "total": len(entries),
        "reasons": rejected_reasons[:25],
    }


@router.get("/my-history")
async def get_my_activity_history(
    limit: int = 100,
    current_user: Dict[str, Any] = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Returns the authenticated user's own activity log history for cross-device visibility.
    """
    uid = current_user.get("sub") or current_user.get("uid")
    items = await db.list_activity_logs(uid=uid, limit=limit)
    return {"success": True, "uid": uid, "count": len(items), "items": items}


@router.get("/log/{uid}")
async def get_user_log(
    uid: str,
    limit: int = 200,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Returns the most recent activity log entries for a user.
    Accessible only by an administrator or the user themselves.
    """
    caller_uid = current_user.get("sub") or current_user.get("uid")
    is_admin = current_user.get("role") == "admin"
    if not is_admin and caller_uid != uid:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Cannot access another user's activity log",
        )

    if limit <= 0 or limit > 1000:
        limit = 200
    items = await db.list_activity_logs(uid=uid, limit=limit)
    return {"success": True, "uid": uid, "count": len(items), "items": items}

