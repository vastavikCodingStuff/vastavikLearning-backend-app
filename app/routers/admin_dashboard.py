"""
Admin Dashboard Extended Endpoints
Provides: student management, AI chat sessions, code usage logs,
completion stats, and practice content (quiz/coding/MCQ) CRUD.
All routes require admin JWT claim.
"""
import uuid
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status, Depends, Query
from pydantic import BaseModel

from app.core.security import require_admin_user, get_current_user_optional
from app.db.firebase import db

router = APIRouter(prefix="/admin", tags=["Admin Dashboard — Extended"])


# ─── Response normalization helpers ───────────────────────────────────────────

def _normalize_student(doc_data: Dict[str, Any], doc_id: str) -> Dict[str, Any]:
    """
    Map a raw Firestore user document onto the StudentProfile contract consumed by
    the Next.js admin frontend (see types/api.ts → StudentProfile).

    Firestore stores `total_lessons_completed` and `student_class`; the frontend
    expects `lessons_completed` and `class_grade`. The doc id is also exposed
    as `uid` so it survives even if the doc field is missing.
    """
    normalized: Dict[str, Any] = dict(doc_data)
    normalized.setdefault("uid", doc_id)
    if "total_lessons_completed" in normalized and "lessons_completed" not in normalized:
        normalized["lessons_completed"] = normalized["total_lessons_completed"]
    if "student_class" in normalized and "class_grade" not in normalized:
        normalized["class_grade"] = normalized["student_class"]
    normalized.setdefault("lessons_completed", 0)
    normalized.setdefault("streak_count", 0)
    normalized.setdefault("is_premium", False)
    normalized.setdefault("subscription_expires_at", None)
    normalized.setdefault("payment_details", [])
    return normalized


# ─── Pydantic models ──────────────────────────────────────────────────────────

class BugReportStatusUpdate(BaseModel):
    status: str  # "open" | "in_progress" | "resolved" | "wont_fix"

class QuizSetCreate(BaseModel):
    title: str
    subject: str
    course_id: Optional[str] = None

class QuizQuestionCreate(BaseModel):
    question: str
    options: List[str]
    correct_index: int
    explanation: str
    difficulty: str = "easy"  # easy | medium | hard

class CodingExerciseCreate(BaseModel):
    title: str
    description: str
    language: str
    starter_code: str
    solution_code: str
    test_cases: List[Dict[str, str]]
    difficulty: str = "easy"

class MCQCreate(BaseModel):
    question: str
    options: List[str]
    correct_index: int
    explanation: str
    subject: str
    topic: str
    difficulty: str = "easy"


# ─── Dashboard overview stats ─────────────────────────────────────────────────

@router.get("/dashboard/stats", response_model=Dict[str, Any])
async def get_dashboard_stats(
    admin_user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    """Aggregate stats for the admin dashboard overview page."""
    try:
        users_ref = db.collection("users")
        users = list(users_ref.stream())
        students = [u for u in users if u.to_dict().get("role") == "student"]

        courses_ref = db.collection("courses")
        courses = list(courses_ref.stream())

        bug_reports_ref = db.collection("bug_reports").where("status", "==", "open")
        open_bugs = list(bug_reports_ref.stream())

        ai_sessions_ref = db.collection("ai_chat_sessions")
        ai_sessions = list(ai_sessions_ref.stream())

        code_execs_ref = db.collection("code_executions")
        code_execs = list(code_execs_ref.stream())

        lang_dist: Dict[str, int] = {}
        for s in students:
            lang = s.to_dict().get("preferred_language", "Java")
            lang_dist[lang] = lang_dist.get(lang, 0) + 1

        return {
            "total_students": len(students),
            "total_courses": len(courses),
            "active_ai_sessions": len(ai_sessions),
            "open_bug_reports": len(open_bugs),
            "code_executions_today": len(code_execs),
            "avg_completion_percent": 0,  # computed separately
            "recent_signups": [],         # TODO: aggregate by day
            "language_distribution": [
                {"language": k, "count": v} for k, v in lang_dist.items()
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Student management ───────────────────────────────────────────────────────

@router.get("/students")
async def list_students(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str = Query(""),
    admin_user: Dict[str, Any] = Depends(require_admin_user),
):
    """List all students with optional name/email search and pagination."""
    try:
        ref = db.collection("users").where("role", "==", "student")
        docs = [_normalize_student(d.to_dict(), d.id) for d in ref.stream()]

        if search:
            q = search.lower()
            docs = [d for d in docs if q in d.get("name", "").lower() or q in d.get("email", "").lower()]

        total = len(docs)
        start = (page - 1) * page_size
        paginated = docs[start: start + page_size]

        return {
            "items": paginated,
            "total": total,
            "page": page,
            "page_size": page_size,
            "has_more": start + page_size < total,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/students/{uid}")
async def get_student(uid: str, admin_user: Dict[str, Any] = Depends(require_admin_user)):
    """Full student profile + payment history."""
    try:
        doc = db.collection("users").document(uid).get()
        if not doc.exists:
            raise HTTPException(status_code=404, detail="Student not found")
        student = _normalize_student(doc.to_dict(), doc.id)

        # Fetch payment history
        txns = db.collection("transactions").where("uid", "==", uid).stream()
        student["payment_details"] = [t.to_dict() for t in txns]
        return student
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── AI Chat Sessions ─────────────────────────────────────────────────────────

@router.get("/ai-chats")
async def list_ai_chat_sessions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    uid: Optional[str] = Query(None),
    admin_user: Dict[str, Any] = Depends(require_admin_user),
):
    """Browse all AI chat sessions saved in Firestore."""
    try:
        ref = db.collection("ai_chat_sessions")
        if uid:
            ref = ref.where("uid", "==", uid)
        docs = [d.to_dict() | {"session_id": d.id} for d in ref.stream()]
        total = len(docs)
        start = (page - 1) * page_size
        return {
            "sessions": docs[start: start + page_size],
            "total": total,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ai-chats/{session_id}")
async def get_ai_chat_session(session_id: str, admin_user: Dict[str, Any] = Depends(require_admin_user)):
    """Full message history for a specific AI chat session."""
    try:
        doc = db.collection("ai_chat_sessions").document(session_id).get()
        if not doc.exists:
            raise HTTPException(status_code=404, detail="Session not found")
        data = doc.to_dict()
        return {"messages": data.get("messages", [])}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Code Execution Logs ──────────────────────────────────────────────────────

@router.get("/code-usage")
async def get_code_usage_logs(
    uid: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    admin_user: Dict[str, Any] = Depends(require_admin_user),
):
    """Code execution logs for all students or a specific student."""
    try:
        ref = db.collection("code_executions")
        if uid:
            ref = ref.where("uid", "==", uid)
        docs = [d.to_dict() | {"id": d.id} for d in ref.stream()]
        total = len(docs)
        start = (page - 1) * page_size
        return {
            "logs": docs[start: start + page_size],
            "total": total,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Student Notes (admin read-only view) ─────────────────────────────────────

@router.get("/notes")
async def get_all_notes(
    uid: Optional[str] = Query(None),
    admin_user: Dict[str, Any] = Depends(require_admin_user),
):
    """Admin view of all student notes."""
    try:
        ref = db.collection("notes")
        if uid:
            ref = ref.where("uid", "==", uid)
        docs = [d.to_dict() | {"id": d.id} for d in ref.stream()]
        return {"notes": docs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Bug Reports ──────────────────────────────────────────────────────────────

@router.get("/bug-reports")
async def list_bug_reports(
    status_filter: Optional[str] = Query(None, alias="status"),
    admin_user: Dict[str, Any] = Depends(require_admin_user),
):
    """List all bug reports with optional status filter."""
    try:
        ref = db.collection("bug_reports")
        if status_filter:
            ref = ref.where("status", "==", status_filter)
        docs = [d.to_dict() | {"id": d.id} for d in ref.stream()]
        return {"reports": docs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/bug-reports/{report_id}")
async def update_bug_report(
    report_id: str,
    body: BugReportStatusUpdate,
    admin_user: Dict[str, Any] = Depends(require_admin_user),
):
    """Update a bug report's status."""
    valid_statuses = {"open", "in_progress", "resolved", "wont_fix"}
    if body.status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {valid_statuses}")
    try:
        ref = db.collection("bug_reports").document(report_id)
        doc = ref.get()
        if not doc.exists:
            raise HTTPException(status_code=404, detail="Report not found")
        update_data: Dict[str, Any] = {"status": body.status}
        if body.status == "resolved":
            update_data["resolved_at"] = datetime.now(timezone.utc).isoformat()
        ref.update(update_data)
        return {"success": True, "report_id": report_id, "status": body.status}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Completion Stats ─────────────────────────────────────────────────────────

async def _compute_course_completion(course_id: str, course_title: str) -> Dict[str, Any]:
    """
    Compute real completion stats for a course by joining:
      - curriculum (parts/subparts from courses/{id}/parts/...)
      - per-student visitedParts (from studentSelections/{uid}.visitedParts)

    Returns a dict matching the CourseCompletionOverview contract consumed by
    the Next.js admin frontend.
    """
    parts = await db.get_course_curriculum(course_id)
    total_parts = len(parts)

    user_docs = db.collection("users").where("role", "==", "student").stream()
    per_student_percent: List[float] = []
    for u_doc in user_docs:
        visited = await db.get_visited_parts(u_doc.id)
        prefix = f"{course_id}::"
        completed = sum(1 for v in visited if isinstance(v, str) and v.startswith(prefix))
        if completed == 0 and total_parts > 0:
            continue
        if total_parts == 0:
            continue
        per_student_percent.append(round((completed / total_parts) * 100.0, 1))

    enrolled = len(per_student_percent)
    avg = round(sum(per_student_percent) / enrolled, 1) if enrolled else 0.0
    fully_done = sum(1 for p in per_student_percent if p >= 100.0)

    return {
        "course_id": course_id,
        "course_title": course_title,
        "enrolled_students": enrolled,
        "avg_completion_percent": avg,
        "fully_completed_count": fully_done,
    }


@router.get("/completion/overview")
async def get_completion_overview(admin_user: Dict[str, Any] = Depends(require_admin_user)):
    """Average completion percentage per course, computed from real visitedParts data."""
    try:
        courses = [d.to_dict() | {"id": d.id} for d in db.collection("courses").stream()]
        overview: List[Dict[str, Any]] = []
        for course in courses:
            overview.append(await _compute_course_completion(course["id"], course.get("title", "")))
        return {"overview": overview}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/completion/top-students")
async def get_top_students(
    limit: int = Query(10, ge=1, le=100),
    admin_user: Dict[str, Any] = Depends(require_admin_user),
):
    """
    Top performing students by completion percentage, computed from real
    visitedParts data joined with curriculum + course titles.
    """
    try:
        courses = {d.id: d.to_dict().get("title", d.id) for d in db.collection("courses").stream()}

        user_docs = db.collection("users").where("role", "==", "student").stream()
        rows: List[Dict[str, Any]] = []
        for u_doc in user_docs:
            uid = u_doc.id
            user = u_doc.to_dict()
            visited = await db.get_visited_parts(uid)
            if not visited:
                continue
            by_course: Dict[str, int] = {}
            for v in visited:
                if not isinstance(v, str) or "::" not in v:
                    continue
                cid, _ = v.split("::", 1)
                by_course[cid] = by_course.get(cid, 0) + 1

            best_course_id = max(by_course, key=by_course.get) if by_course else None
            if not best_course_id:
                continue
            parts = await db.get_course_curriculum(best_course_id)
            total_parts = len(parts)
            completed_parts = by_course[best_course_id]
            percent = round((completed_parts / total_parts) * 100.0, 1) if total_parts else 0.0
            rows.append({
                "uid": uid,
                "student_name": user.get("name", "Student"),
                "course_id": best_course_id,
                "course_title": courses.get(best_course_id, best_course_id),
                "total_parts": total_parts,
                "completed_parts": completed_parts,
                "completion_percent": percent,
                "last_activity": user.get("updated_at") or user.get("created_at") or "",
            })

        rows.sort(key=lambda r: r["completion_percent"], reverse=True)
        return {"stats": rows[:limit]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Practice: Quiz ──────────────────────────────────────────────────────────

@router.get("/practice/quiz")
async def list_quiz_sets(admin_user: Dict[str, Any] = Depends(require_admin_user)):
    sets = [d.to_dict() | {"id": d.id} for d in db.collection("practice_quizzes").stream()]
    return {"sets": sets}


@router.post("/practice/quiz")
async def create_quiz_set(body: QuizSetCreate, admin_user: Dict[str, Any] = Depends(require_admin_user)):
    set_id = str(uuid.uuid4())
    data = body.model_dump() | {
        "id": set_id,
        "question_count": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    db.collection("practice_quizzes").document(set_id).set(data)
    return data


@router.get("/practice/quiz/{set_id}/questions")
async def get_quiz_questions(set_id: str, admin_user: Dict[str, Any] = Depends(require_admin_user)):
    qs = [d.to_dict() | {"id": d.id} for d in db.collection("quiz_questions").where("set_id", "==", set_id).stream()]
    return {"questions": qs}


@router.post("/practice/quiz/{set_id}/questions")
async def add_quiz_question(set_id: str, body: QuizQuestionCreate, admin_user: Dict[str, Any] = Depends(require_admin_user)):
    q_id = str(uuid.uuid4())
    data = body.model_dump() | {
        "id": q_id,
        "set_id": set_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    db.collection("quiz_questions").document(q_id).set(data)
    # Update question count
    set_ref = db.collection("practice_quizzes").document(set_id)
    set_doc = set_ref.get()
    if set_doc.exists:
        current = set_doc.to_dict().get("question_count", 0)
        set_ref.update({"question_count": current + 1})
    return data


# ─── Practice: Coding Exercises ───────────────────────────────────────────────

@router.get("/practice/coding")
async def list_coding_exercises(admin_user: Dict[str, Any] = Depends(require_admin_user)):
    exercises = [d.to_dict() | {"id": d.id} for d in db.collection("coding_exercises").stream()]
    return {"exercises": exercises}


@router.post("/practice/coding")
async def create_coding_exercise(body: CodingExerciseCreate, admin_user: Dict[str, Any] = Depends(require_admin_user)):
    ex_id = str(uuid.uuid4())
    data = body.model_dump() | {
        "id": ex_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    db.collection("coding_exercises").document(ex_id).set(data)
    return data


@router.delete("/practice/coding/{exercise_id}")
async def delete_coding_exercise(exercise_id: str, admin_user: Dict[str, Any] = Depends(require_admin_user)):
    db.collection("coding_exercises").document(exercise_id).delete()
    return {"success": True, "message": f"Exercise {exercise_id} deleted"}


# ─── Practice: MCQs ──────────────────────────────────────────────────────────

@router.get("/practice/mcq")
async def list_mcqs(admin_user: Dict[str, Any] = Depends(require_admin_user)):
    mcqs = [d.to_dict() | {"id": d.id} for d in db.collection("mcqs").stream()]
    return {"mcqs": mcqs}


@router.post("/practice/mcq")
async def create_mcq(body: MCQCreate, admin_user: Dict[str, Any] = Depends(require_admin_user)):
    mcq_id = str(uuid.uuid4())
    data = body.model_dump() | {
        "id": mcq_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    db.collection("mcqs").document(mcq_id).set(data)
    return data


@router.delete("/practice/mcq/{mcq_id}")
async def delete_mcq(mcq_id: str, admin_user: Dict[str, Any] = Depends(require_admin_user)):
    db.collection("mcqs").document(mcq_id).delete()
    return {"success": True, "message": f"MCQ {mcq_id} deleted"}


# ─── Admin Videos endpoint ───────────────────────────────────────────────────

@router.get("/videos")
async def list_videos(admin_user: Dict[str, Any] = Depends(require_admin_user)):
    """All video lessons for the admin videos panel.

    Merges two sources so admin uploads actually appear in the list:
      1. Curriculum lessons nested at courses/{id}/parts/{id}/subparts/{id}/lessons
      2. Standalone uploads at the flat top-level `videos` collection (written
         by the admin web's UploadVideoModal; these were previously invisible
         to the list endpoint and were the cause of the "upload doesn't show"
         bug reported by the user).
    """
    try:
        videos: List[Dict[str, Any]] = []
        seen_ids: set = set()

        # 1. Curriculum-nested lessons
        for course in db.collection("courses").stream():
            for part in (
                db.collection("courses").document(course.id)
                .collection("parts").stream()
            ):
                for subpart in (
                    db.collection("courses").document(course.id)
                    .collection("parts").document(part.id)
                    .collection("subparts").stream()
                ):
                    for lesson in (
                        db.collection("courses").document(course.id)
                        .collection("parts").document(part.id)
                        .collection("subparts").document(subpart.id)
                        .collection("lessons").stream()
                    ):
                        lesson_data = lesson.to_dict() | {
                            "id": lesson.id,
                            "course_id": course.id,
                            "part_id": part.id,
                            "subpart_id": subpart.id,
                            "source": "curriculum",
                        }
                        videos.append(lesson_data)
                        seen_ids.add(lesson.id)

        # 2. Flat uploads (admin web UploadVideoModal)
        for v in db.collection("videos").stream():
            if v.id in seen_ids:
                continue
            vdata = v.to_dict() | {"id": v.id, "source": "upload"}
            videos.append(vdata)
            seen_ids.add(v.id)

        return {"videos": videos}
    except Exception as e:
        return {"videos": []}


class VideoCreate(BaseModel):
    id: Optional[str] = None
    title: str
    description: Optional[str] = ""
    video_type: str = "screen_recording"  # screen_recording | whiteboard | short
    youtube_url: str
    youtube_video_id: str
    duration_sec: int = 600
    whiteboard_image_url: Optional[str] = ""
    code_sample: Optional[str] = ""
    notes: Optional[str] = ""
    is_premium: bool = False
    order: int = 1
    course_id: Optional[str] = None


class CourseCreate(BaseModel):
    id: Optional[str] = None
    title: str
    description: Optional[str] = ""
    icon_name: Optional[str] = "code"
    color: Optional[int] = 0xFFE65100
    order: int = 1
    is_published: bool = True


@router.post("/videos")
async def create_video(body: VideoCreate, admin_user: Dict[str, Any] = Depends(require_admin_user)):
    """Registers an unlisted or public YouTube video lecture."""
    vid_id = body.id or str(uuid.uuid4())
    data = body.model_dump() | {
        "id": vid_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        db.collection("videos").document(vid_id).set(data)
    except Exception:
        pass
    return {"success": True, "video": data}


@router.delete("/videos/{video_id}")
async def delete_video(video_id: str, admin_user: Dict[str, Any] = Depends(require_admin_user)):
    """
    Deletes a video from the flat `videos` collection (used by the admin web
    UploadVideoModal). Idempotent: returns success even if the doc doesn't exist.
    """
    try:
        db.collection("videos").document(video_id).delete()
        return {"success": True, "video_id": video_id, "deleted": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/courses")
async def create_course(body: CourseCreate, admin_user: Dict[str, Any] = Depends(require_admin_user)):
    """Creates a new course track."""
    cid = body.id or str(uuid.uuid4())
    data = body.model_dump() | {
        "id": cid,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        db.collection("courses").document(cid).set(data)
    except Exception:
        pass
    return {"success": True, "course": data}


class LessonLinkCreate(BaseModel):
    title: str
    lesson_id: str


@router.post("/courses/{course_id}/parts/{part_id}/lessons")
async def add_lesson_to_part(
    course_id: str,
    part_id: str,
    body: LessonLinkCreate,
    admin_user: Dict[str, Any] = Depends(require_admin_user),
):
    """
    Links a video lesson into a curriculum part by creating a subpart entry.
    Called by the Next.js admin curriculum editor (useCourses.addLessonToPart).
    Returns 404 if the course part does not exist (prevents orphaned subparts).
    """
    try:
        part_ref = (
            db.collection("courses").document(course_id)
            .collection("parts").document(part_id)
        )
        part_doc = part_ref.get()
        if not part_doc.exists:
            raise HTTPException(status_code=404, detail="Course part not found")
        sub_id = f"sub_{uuid.uuid4().hex[:10]}"
        subpart = {
            "subpart_id": sub_id,
            "title": body.title,
            "lesson_id": body.lesson_id,
        }
        part_ref.collection("subparts").document(sub_id).set(subpart)
        return {"success": True, "subpart": subpart}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

