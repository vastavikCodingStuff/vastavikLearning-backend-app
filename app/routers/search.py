from typing import Dict, Any
from fastapi import APIRouter, Query, Depends

from app.core.rate_limiter import rate_limit
from app.db.firebase import db

router = APIRouter(prefix="/api/v1", tags=["Global Search"])


@router.get("/search", dependencies=[Depends(rate_limit("general"))])
async def search_content(q: str = Query(..., min_length=1, description="Search term")):
    """
    Performs full-text keyword search across courses, topics, and lesson catalog.
    """
    results = await db.search(q)
    return results
