import uuid
from datetime import datetime, timezone
from typing import Dict, Any
from fastapi import APIRouter, Query, Depends

from app.core.rate_limiter import rate_limit
from app.db.firebase import db
from app.services.moderation import analyze_content_safety

router = APIRouter(prefix="/api/v1", tags=["Global Search"])


@router.get("/search", dependencies=[Depends(rate_limit("general"))])
async def search_content(q: str = Query(..., min_length=1, description="Search term")):
    """
    Performs full-text keyword search across courses, topics, and lesson catalog,
    while auditing and flagging inappropriate/bad search queries for administration.
    """
    try:
        mod = analyze_content_safety(q)
        log_id = f"srch_{uuid.uuid4().hex[:12]}"
        db.collection("search_logs").document(log_id).set({
            "id": log_id,
            "query": q,
            "is_flagged": mod["is_flagged"],
            "flag_reasons": mod["flag_reasons"],
            "flagged_terms": mod["flagged_terms"],
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        pass

    results = await db.search(q)
    return results
