"""Activity log ingestion endpoint.

Accepts batches of activity-log JSON entries from the Android app and persists
them to Firestore (collection `activity_logs`) and the in-memory fallback store.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.rate_limiter import rate_limit
from app.db.firebase import db

logger = logging.getLogger("vastavik.activity")
router = APIRouter(prefix="/api/v1/activity", tags=["Activity Log"])


@router.post("/log", dependencies=[Depends(rate_limit("general"))])
async def ingest_log(request: Request) -> Dict[str, Any]:
    """
    Accept a JSON array of activity log entries (or a single object) and persist
    them. Each entry MUST be a JSON object with at least an "id" and an "event"
    field. Returns the number of accepted entries.
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

    accepted = 0
    rejected = 0
    rejected_reasons: List[str] = []

    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            rejected += 1
            rejected_reasons.append(f"#{idx}: not an object")
            continue
        if "event" not in entry or "id" not in entry:
            rejected += 1
            rejected_reasons.append(f"#{idx}: missing event/id")
            continue

        entry.setdefault("received_at", datetime.now(timezone.utc).isoformat())
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


@router.get("/log/{uid}")
async def get_user_log(uid: str, limit: int = 200) -> Dict[str, Any]:
    """
    Admin-only — returns the most recent activity log entries for a user.
    """
    if limit <= 0 or limit > 1000:
        limit = 200
    items = await db.list_activity_logs(uid=uid, limit=limit)
    return {"success": True, "uid": uid, "count": len(items), "items": items}
