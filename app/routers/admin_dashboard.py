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

from app.core.security import require_admin_user
from app.db.firebase import db

router = APIRouter(prefix="/admin", tags=["Admin Dashboard — Extended"])


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
async def get_dashboard_stats(admin_user: Dict[str, Any] = Depends(require_admin_user)):
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
        docs = [d.to_dict() for d in ref.stream()]

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
        student = doc.to_dict()

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

@router.get("/completion/overview")
async def get_completion_overview(admin_user: Dict[str, Any] = Depends(require_admin_user)):
    """Average completion percentage per course."""
    try:
        courses = [d.to_dict() | {"id": d.id} for d in db.collection("courses").stream()]
        result = []
        for course in courses:
            completions = db.collection("course_completions").where("course_id", "==", course["id"]).stream()
            completion_docs = [c.to_dict() for c in completions]
            if completion_docs:
                avg = sum(c.get("completion_percent", 0) for c in completion_docs) / len(completion_docs)
                fully_done = sum(1 for c in completion_docs if c.get("completion_percent", 0) >= 100)
            else:
                avg, fully_done = 0.0, 0
            result.append({
                "course_id": course["id"],
                "course_title": course.get("title", ""),
                "enrolled_students": len(completion_docs),
                "avg_completion_percent": round(avg, 1),
                "fully_completed_count": fully_done,
            })
        return {"overview": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/completion/top-students")
async def get_top_students(
    limit: int = Query(10, ge=1, le=100),
    admin_user: Dict[str, Any] = Depends(require_admin_user),
):
    """Top performing students by completion percentage."""
    try:
        docs = db.collection("course_completions").stream()
        stats = [d.to_dict() for d in docs]
        stats.sort(key=lambda x: x.get("completion_percent", 0), reverse=True)
        return {"stats": stats[:limit]}
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
    """All video lessons across all courses for the admin videos panel."""
    try:
        # Fetch all lessons from all courses/parts
        videos = []
        courses = db.collection("courses").stream()
        for course in courses:
            parts = db.collection("courses").document(course.id).collection("parts").stream()
            for part in parts:
                subparts = db.collection("courses").document(course.id).collection("parts").document(part.id).collection("subparts").stream()
                for subpart in subparts:
                    lessons = db.collection("courses").document(course.id).collection("parts").document(part.id).collection("subparts").document(subpart.id).collection("lessons").stream()
                    for lesson in lessons:
                        lesson_data = lesson.to_dict() | {"id": lesson.id, "course_id": course.id, "part_id": part.id}
                        videos.append(lesson_data)
        return {"videos": videos}
    except Exception as e:
        # Fallback empty list if collections empty
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

