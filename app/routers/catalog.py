import time
from typing import Optional, Dict, Any
from fastapi import APIRouter, HTTPException, status, Depends

from app.core.security import get_current_user
from app.core.rate_limiter import rate_limit
from app.db.firebase import db
from app.models.schemas import (
    HomeCatalogResponse,
    CurriculumResponse,
    LessonResponse,
    VisitedRequest,
    CommonResponse,
)

router = APIRouter(prefix="/api/v1", tags=["Courses & Curriculum"])

# In-Memory 5-Minute TTL Cache for Home Catalog
_catalog_cache: Optional[Dict[str, Any]] = None
_catalog_cache_timestamp: float = 0.0
CATALOG_CACHE_TTL_SECONDS = 300.0  # 5 minutes


@router.get("/catalog/home", response_model=HomeCatalogResponse, dependencies=[Depends(rate_limit("general"))])
async def get_home_catalog():
    """
    Returns home catalog containing courses, banners, and popular topics.
    Results are cached in RAM for 5 minutes to minimize database reads and latency.
    """
    global _catalog_cache, _catalog_cache_timestamp
    now = time.time()

    if _catalog_cache is not None and (now - _catalog_cache_timestamp) < CATALOG_CACHE_TTL_SECONDS:
        return _catalog_cache

    catalog_data = await db.get_home_catalog()
    _catalog_cache = catalog_data
    _catalog_cache_timestamp = now
    return catalog_data


@router.get("/courses/{course_id}/curriculum", response_model=CurriculumResponse, dependencies=[Depends(rate_limit("general"))])
async def get_curriculum(course_id: str):
    """
    Returns the organized parts and subparts for a given course curriculum.
    """
    parts = await db.get_course_curriculum(course_id)
    return CurriculumResponse(course_id=course_id, parts=parts)


@router.get("/lessons/{lesson_id}", response_model=LessonResponse, dependencies=[Depends(rate_limit("general"))])
async def get_lesson(lesson_id: str, current_user: Optional[Dict[str, Any]] = Depends(get_current_user)):
    """
    Returns lesson content and metadata.
    Enforces premium access check for protected masterclass lessons.
    """
    lesson = await db.get_lesson(lesson_id)
    if not lesson:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lesson not found.")

    if lesson.get("is_premium", False):
        if not current_user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required to view premium lesson content.",
            )
        uid = current_user.get("sub")
        user = await db.get_user_by_id(uid)
        is_premium_user = (user and user.get("is_premium", False)) or (current_user.get("role") == "admin")
        if not is_premium_user:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This lesson is part of Vastavik Pro. Please upgrade to unlock.",
            )

    return lesson


@router.post("/progress/visited", response_model=CommonResponse)
async def mark_visited(request: VisitedRequest, current_user: Dict[str, Any] = Depends(get_current_user)):
    """
    Updates the student's learning progress by adding course::part to visited parts.
    """
    uid = current_user.get("sub")
    await db.mark_visited(uid, request.course_id, request.part_id)
    return CommonResponse(success=True, message=f"Part {request.part_id} marked as completed.")
