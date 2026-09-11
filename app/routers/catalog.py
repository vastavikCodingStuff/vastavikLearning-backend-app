import time
from typing import Optional, Dict, Any
from fastapi import APIRouter, HTTPException, status, Depends

from app.core.security import get_current_user, get_current_user_optional
from app.core.rate_limiter import rate_limit
from app.db.firebase import db
from app.models.schemas import (
    HomeCatalogResponse,
    CurriculumResponse,
    LessonResponse,
    VisitedRequest,
    CommonResponse,
    CourseProgressResponse,
    ProgressSummaryResponse,
)

router = APIRouter(prefix="/api/v1", tags=["Courses & Curriculum"])

# In-Memory 5-Minute TTL Cache for Home Catalog
_catalog_cache: Optional[Dict[str, Any]] = None
_catalog_cache_timestamp: float = 0.0
CATALOG_CACHE_TTL_SECONDS = 300.0  # 5 minutes


def invalidate_catalog_cache():
    """Invalidate cached home catalog so newly created/modified courses appear immediately."""
    global _catalog_cache, _catalog_cache_timestamp
    _catalog_cache = None
    _catalog_cache_timestamp = 0.0


@router.get("/catalog/home", response_model=HomeCatalogResponse, dependencies=[Depends(rate_limit("general"))])
async def get_home_catalog(force: bool = False):
    """
    Returns home catalog containing courses, banners, and popular topics.
    Results are cached in RAM for 5 minutes to minimize database reads and latency.
    Pass ?force=true to bypass cache (used by Learn pull-to-refresh).
    """
    global _catalog_cache, _catalog_cache_timestamp
    now = time.time()

    if not force and _catalog_cache is not None and (now - _catalog_cache_timestamp) < CATALOG_CACHE_TTL_SECONDS:
        return _catalog_cache

    catalog_data = await db.get_home_catalog()
    import logging
    try:
        courses = catalog_data.get("courses", [])
        if not courses:
            logging.getLogger("vastavik.catalog").warning(
                "get_home_catalog returned 0 courses (live=%s). Check Firestore 'courses' collection and credentials.",
                getattr(db, "use_live_firestore", False),
            )
    except Exception:
        pass
    _catalog_cache = catalog_data
    _catalog_cache_timestamp = now
    return catalog_data


@router.get("/courses/{course_id}/curriculum", response_model=CurriculumResponse, dependencies=[Depends(rate_limit("general"))])
async def get_curriculum(course_id: str, force: bool = False):
    """
    Returns the organized parts and subparts for a given course curriculum.
    Pass ?force=true to ensure fresh data after admin edits.
    """
    # Curriculum is not cached server-side currently, but force param is kept for parity and future cache
    parts = await db.get_course_curriculum(course_id)
    return CurriculumResponse(course_id=course_id, parts=parts)


@router.get("/lessons/{lesson_id}", response_model=LessonResponse, dependencies=[Depends(rate_limit("general"))])
async def get_lesson(lesson_id: str, current_user: Optional[Dict[str, Any]] = Depends(get_current_user_optional)):
    """
    Returns lesson content and metadata across 3 lecture formats:
    - screen_recording: Computer screen / VS Code coding sessions
    - whiteboard: Conceptual whiteboard explanations
    - short: Quick vertical 1-2 minute high-yield shorts
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

    # Normalize camelCase to snake_case if fetched from legacy or direct Firestore
    if "youtube_url" not in lesson and "youtubeUrl" in lesson:
        lesson["youtube_url"] = lesson["youtubeUrl"]
    if "youtube_video_id" not in lesson and "youtubeVideoId" in lesson:
        lesson["youtube_video_id"] = lesson["youtubeVideoId"]
    if "whiteboard_image_url" not in lesson:
        lesson["whiteboard_image_url"] = lesson.get("whiteboardImageUrl", "")
    if "code_sample" not in lesson:
        lesson["code_sample"] = lesson.get("codeSample", "")
    if "duration_sec" not in lesson:
        lesson["duration_sec"] = lesson.get("durationSec", 0)
    if "description" not in lesson or lesson["description"] is None:
        lesson["description"] = ""
    if "shorts_url" not in lesson:
        lesson["shorts_url"] = lesson.get("shortsUrl", "") or lesson.get("shorts_url", "")
    if "shorts_video_id" not in lesson:
        lesson["shorts_video_id"] = lesson.get("shortsVideoId", "") or lesson.get("shorts_video_id", "")
    if "privacy" not in lesson:
        lesson["privacy"] = lesson.get("privacy", "unlisted")
    if "is_published" not in lesson and "isPublished" in lesson:
        lesson["is_published"] = lesson["isPublished"]

    # Normalize format for Android client (vscode/screen/shorts -> canonical)
    fmt = lesson.get("video_format") or lesson.get("videoFormat") or "screen_recording"
    if fmt in ("vscode", "screen", "screen_recording"):
        fmt = "screen_recording"
    elif fmt in ("shorts", "short"):
        fmt = "short"
        if not lesson.get("shorts_url"):
            lesson["shorts_url"] = lesson.get("youtube_url", "")
        if not lesson.get("shorts_video_id"):
            lesson["shorts_video_id"] = lesson.get("youtube_video_id", "")
    elif fmt == "whiteboard":
        fmt = "whiteboard"
    lesson["video_format"] = fmt
    return lesson


@router.post("/progress/visited", response_model=CommonResponse)
async def mark_visited(request: VisitedRequest, current_user: Dict[str, Any] = Depends(get_current_user)):
    """
    Updates the student's learning progress by adding course::part to visited parts.
    """
    uid = current_user.get("sub")
    await db.mark_visited(uid, request.course_id, request.part_id)
    return CommonResponse(success=True, message=f"Part {request.part_id} marked as completed.")


@router.get("/courses/{course_id}/progress", response_model=CourseProgressResponse)
async def get_course_progress(course_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """
    Calculates student completion rate for a specific course.
    """
    uid = current_user.get("sub")
    parts = await db.get_course_curriculum(course_id)
    total_parts = len(parts)

    visited_all = await db.get_visited_parts(uid)
    prefix = f"{course_id}::"
    course_visited = [p for p in visited_all if p.startswith(prefix)]
    completed_count = len(course_visited)

    percent = round((completed_count / total_parts * 100.0), 1) if total_parts > 0 else 0.0

    return CourseProgressResponse(
        course_id=course_id,
        course_title=course_id.replace("course_", "").replace("_", " ").title(),
        total_parts=total_parts,
        completed_parts=completed_count,
        completion_percent=percent,
        visited_part_ids=course_visited,
    )


@router.get("/progress/summary", response_model=ProgressSummaryResponse)
async def get_progress_summary(current_user: Dict[str, Any] = Depends(get_current_user)):
    """
    Aggregates overall course completion rates for the authenticated student.
    Fast zero-copy in-memory calculation.
    """
    uid = current_user.get("sub")
    catalog = await db.get_home_catalog()
    courses = catalog.get("courses", [])

    visited_all = await db.get_visited_parts(uid)
    course_progress_list = []
    total_percent_sum = 0.0

    for c in courses:
        cid = c.get("id", "")
        parts = await db.get_course_curriculum(cid)
        total_parts = len(parts)
        prefix = f"{cid}::"
        course_visited = [p for p in visited_all if p.startswith(prefix)]
        completed_count = len(course_visited)
        percent = round((completed_count / total_parts * 100.0), 1) if total_parts > 0 else 0.0

        course_progress_list.append(
            CourseProgressResponse(
                course_id=cid,
                course_title=c.get("title", cid),
                total_parts=total_parts,
                completed_parts=completed_count,
                completion_percent=percent,
                visited_part_ids=course_visited,
            )
        )
        total_percent_sum += percent

    overall = round(total_percent_sum / len(courses), 1) if courses else 0.0

    return ProgressSummaryResponse(
        total_courses_enrolled=len(courses),
        overall_completion_percent=overall,
        courses=course_progress_list,
    )
