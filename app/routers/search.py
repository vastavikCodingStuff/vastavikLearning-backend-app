import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, Query, Depends

from app.core.rate_limiter import rate_limit
from app.core.security import get_current_user_optional, get_current_user
from app.db.firebase import db
from app.services.moderation import analyze_content_safety

router = APIRouter(prefix="/api/v1", tags=["Global Search"])


@router.get("/search", dependencies=[Depends(rate_limit("general"))])
async def search_content(
    q: str = Query(..., min_length=1, description="Search term"),
    current_user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    """
    Performs full-text keyword search across courses, topics, and lesson catalog,
    while auditing and flagging inappropriate/bad search queries for administration,
    associating the search with the active user account.
    """
    uid = (current_user.get("sub") or current_user.get("uid")) if current_user else "anonymous"
    student_name = current_user.get("name", "Student") if current_user else "Student"
    student_email = current_user.get("email", "") if current_user else ""
    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        mod = analyze_content_safety(q)
        log_id = f"srch_{uuid.uuid4().hex[:12]}"
        search_record = {
            "id": log_id,
            "uid": uid,
            "student_name": student_name,
            "student_email": student_email,
            "query": q,
            "is_flagged": mod["is_flagged"],
            "flag_reasons": mod["flag_reasons"],
            "flagged_terms": mod["flagged_terms"],
            "created_at": now_iso,
        }
        db.collection("search_logs").document(log_id).set(search_record)

        # Mirror to user activity log
        await db.save_activity_log({
            "id": f"act_{log_id}",
            "uid": uid,
            "student_name": student_name,
            "student_email": student_email,
            "event": "SEARCH",
            "query": q,
            "response": f"Searched catalog for: {q}",
            "metadata": {"is_flagged": mod["is_flagged"]},
            "timestamp": now_iso,
            "received_at": now_iso,
        })
    except Exception:
        pass

    results = await db.search(q)
    return results


@router.get("/search/history", dependencies=[Depends(rate_limit("general"))])
async def get_search_history(
    uid: Optional[str] = None,
    limit: int = 50,
    current_user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    """
    Returns past search queries for the user so they appear when opening the app with their account.
    """
    target_uid = uid or ((current_user.get("sub") or current_user.get("uid")) if current_user else None)
    if not target_uid:
        return []

    try:
        docs = db.collection("search_logs").where("uid", "==", target_uid).stream()
        results = [d.to_dict() | {"id": d.id} for d in docs]
        results.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return results[:limit]
    except Exception:
        return []

