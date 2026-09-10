"""
Admin Dashboard Extended Endpoints
Provides: student management, AI chat sessions, code usage logs,
completion stats, and practice content (quiz/coding/MCQ) CRUD.
All routes require admin JWT claim.
"""
import json
import logging
import re
import uuid
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

logger = logging.getLogger("vastavik.admin_dashboard")

from fastapi import APIRouter, HTTPException, status, Depends, Query, UploadFile, File, Form
from pydantic import BaseModel

from app.core.security import require_admin_user, get_current_user_optional
from app.db.firebase import db

router = APIRouter(prefix="/admin", tags=["Admin Dashboard — Extended"])


# ─── Response normalization helpers ───────────────────────────────────────────

def _normalize_student(doc_data: Dict[str, Any], doc_id: str, selection_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Map a raw Firestore user document onto the StudentProfile contract consumed by
    the Next.js admin frontend (see types/api.ts → StudentProfile).
    Supports camelCase and snake_case, joins studentSelections for enrolled course,
    and calculates completed lessons.
    """
    normalized: Dict[str, Any] = dict(doc_data)
    normalized["uid"] = normalized.get("uid") or doc_id

    # Name
    name = normalized.get("name") or normalized.get("displayName") or "Student"
    normalized["name"] = name

    # Email
    email = normalized.get("email")
    if not email:
        phone = normalized.get("phone") or normalized.get("phoneNumber")
        if phone:
            email = f"{phone}@student.vastavik"
        else:
            email = f"student_{doc_id[:8]}@vastavik.com"
    normalized["email"] = email

    # Preferred language
    pref_lang = normalized.get("preferred_language") or normalized.get("preferredLanguage") or "Java"
    normalized["preferred_language"] = pref_lang

    # Board
    board = normalized.get("board") or "ICSE"
    normalized["board"] = board

    # Class / Grade
    student_class = normalized.get("student_class") or normalized.get("studentClass") or normalized.get("class_grade") or ""
    normalized["student_class"] = student_class
    normalized["class_grade"] = student_class

    # School
    school = normalized.get("school") or ""
    normalized["school"] = school

    # Date of birth
    dob = normalized.get("dob") or normalized.get("dateOfBirth") or normalized.get("date_of_birth") or ""
    normalized["dob"] = dob
    normalized["date_of_birth"] = dob

    # Premium status
    is_prem = normalized.get("is_premium") if "is_premium" in normalized else normalized.get("isPremium", False)
    normalized["is_premium"] = bool(is_prem)

    # Enrollment data from studentSelections
    enrolled_course_name = None
    enrolled_course_id = None
    visited_parts: List[Any] = []
    if selection_data:
        enrolled_course_name = selection_data.get("courseName") or selection_data.get("course_name")
        enrolled_course_id = selection_data.get("courseId") or selection_data.get("course_id")
        visited_parts = selection_data.get("visitedParts") or []

    normalized["enrolled_course"] = enrolled_course_name
    normalized["enrolled_course_id"] = enrolled_course_id

    # Lessons completed
    existing_completed = normalized.get("lessons_completed") or normalized.get("total_lessons_completed") or 0
    normalized["lessons_completed"] = max(int(existing_completed), len(visited_parts))
    normalized["streak_count"] = int(normalized.get("streak_count", 0))

    # Created At
    created_at = normalized.get("created_at") or normalized.get("createdAt")
    if hasattr(created_at, "isoformat"):
        created_at = created_at.isoformat()
    elif not created_at:
        created_at = datetime.now(timezone.utc).isoformat()
    normalized["created_at"] = str(created_at)

    normalized.setdefault("subscription_expires_at", None)
    normalized.setdefault("payment_details", [])
    normalized.setdefault("role", "student")

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
        students = [
            u for u in users
            if u.to_dict().get("role") == "student"
            or u.to_dict().get("studentClass")
            or u.to_dict().get("board")
        ]

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
            d = s.to_dict()
            lang = d.get("preferred_language") or d.get("preferredLanguage") or "Java"
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
        # 1. Fetch studentSelections mapping for fast lookup
        selections: Dict[str, Dict[str, Any]] = {
            d.id: d.to_dict() for d in db.collection("studentSelections").stream()
        }

        # 2. Fetch all users from Firestore
        users_stream = db.collection("users").stream()
        students: List[Dict[str, Any]] = []

        for d in users_stream:
            data = d.to_dict()
            # Exclude admin accounts
            if data.get("role") == "admin" or data.get("email") == "admin@vastaviklearning.com":
                continue

            # Include if explicitly role=='student', or has selection, or has student profile attributes,
            # or role is None.
            role = data.get("role")
            is_student = (
                role == "student"
                or d.id in selections
                or bool(data.get("board") or data.get("studentClass") or data.get("school"))
                or role is None
            )

            # Skip empty placeholder docs created by tests that have no name, no email, no board, no role
            if not is_student or (not data.get("name") and not data.get("email") and d.id not in selections):
                continue

            sel_data = selections.get(d.id)
            normalized = _normalize_student(data, d.id, sel_data)
            students.append(normalized)

        # Also check if any uid in selections wasn't in users collection
        existing_uids = {s["uid"] for s in students}
        for sel_uid, sel_data in selections.items():
            if sel_uid not in existing_uids and not sel_uid.startswith("student_test"):
                students.append(_normalize_student({}, sel_uid, sel_data))

        # 3. Priority sort:
        # Prioritize real enrolled students (those in studentSelections) first,
        # then named students with real names, then most recently created.
        def sort_key(s: Dict[str, Any]):
            has_enrollment = 1 if s.get("enrolled_course") else 0
            name = s.get("name", "")
            is_real_name = 1 if name not in ["Student", "Onboarding Student"] and not s.get("email", "").startswith("student_1") else 0
            has_school = 1 if s.get("school") else 0
            return (has_enrollment, is_real_name, has_school, s.get("created_at", ""))

        students.sort(key=sort_key, reverse=True)

        # 4. Search filter
        if search:
            q = search.lower()
            students = [
                s for s in students
                if q in s.get("name", "").lower()
                or q in s.get("email", "").lower()
                or q in s.get("school", "").lower()
                or q in s.get("board", "").lower()
                or q in (s.get("enrolled_course") or "").lower()
            ]

        total = len(students)
        start = (page - 1) * page_size
        paginated = students[start: start + page_size]

        return {
            "items": paginated,
            "total": total,
            "page": page,
            "page_size": page_size,
            "has_more": start + page_size < total,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/students/archived")
async def list_archived_students(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
    admin_user: Dict[str, Any] = Depends(require_admin_user),
):
    """Lists all students whose accounts have been banned and archived separately."""
    try:
        ref = db.collection("deleted_students_archive")
        docs = [d.to_dict() | {"uid": d.id} for d in ref.stream()]
        docs.sort(key=lambda x: x.get("archived_at", ""), reverse=True)

        if search:
            q = search.lower()
            docs = [
                d for d in docs
                if q in d.get("name", "").lower()
                or q in d.get("email", "").lower()
                or q in d.get("uid", "").lower()
            ]

        total = len(docs)
        start = (page - 1) * page_size
        items = docs[start : start + page_size]
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size if page_size > 0 else 1,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/students/archived/{uid}")
async def get_archived_student(
    uid: str,
    admin_user: Dict[str, Any] = Depends(require_admin_user),
):
    """Retrieves full snapshot details of an archived/banned student."""
    try:
        doc = db.collection("deleted_students_archive").document(uid).get()
        if not doc.exists:
            raise HTTPException(status_code=404, detail="Archived student not found")
        return doc.to_dict() | {"uid": doc.id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/students/{uid}")
async def get_student(uid: str, admin_user: Dict[str, Any] = Depends(require_admin_user)):
    """Full student profile + payment history + activity timeline + search + practice + AI chats."""
    try:
        doc = db.collection("users").document(uid).get()
        user_data = doc.to_dict() if doc.exists else {}

        # Fetch studentSelections for this uid
        sel_doc = db.collection("studentSelections").document(uid).get()
        sel_data = sel_doc.to_dict() if sel_doc.exists else None

        if not doc.exists and not sel_doc.exists:
            raise HTTPException(status_code=404, detail="Student not found")

        student = _normalize_student(user_data, uid, sel_data)

        # Fetch payment history
        txns = db.collection("transactions").where("uid", "==", uid).stream()
        student["payment_details"] = [t.to_dict() for t in txns]

        # 1. Fetch activity log timeline
        try:
            activities = await db.list_activity_logs(uid=uid, limit=100)
            student["activities"] = activities
        except Exception:
            student["activities"] = []

        # 2. Fetch search logs
        try:
            search_docs = db.collection("search_logs").where("uid", "==", uid).stream()
            searches = [d.to_dict() | {"id": d.id} for d in search_docs]
            searches.sort(key=lambda x: x.get("created_at", ""), reverse=True)
            student["searches"] = searches
        except Exception:
            student["searches"] = []

        # 3. Fetch practice attempts (MCQ, Predict Output, Coding)
        try:
            practice_docs = db.collection("practice_attempts").where("uid", "==", uid).stream()
            practice_items = [d.to_dict() | {"id": d.id} for d in practice_docs]
            practice_items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
            student["practice_history"] = practice_items
        except Exception:
            student["practice_history"] = []

        # 4. Fetch AI chat sessions
        try:
            chats = []
            for c in db.collection("ai_chat_sessions").where("uid", "==", uid).stream():
                chats.append(c.to_dict() | {"session_id": c.id})
            chats.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
            student["ai_chats"] = chats
        except Exception:
            student["ai_chats"] = []

        # 5. Fetch code executions
        try:
            code_docs = db.collection("code_executions").where("uid", "==", uid).stream()
            code_execs = [d.to_dict() | {"id": d.id} for d in code_docs]
            code_execs.sort(key=lambda x: x.get("created_at", ""), reverse=True)
            student["code_executions"] = code_execs
        except Exception:
            student["code_executions"] = []

        return student
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/students/{uid}")
async def delete_student(uid: str, admin_user: Dict[str, Any] = Depends(require_admin_user)):
    """
    Permanently bans and deletes a student from active collections,
    archives full snapshot to deleted_students_archive, and revokes all active sessions.
    """
    try:
        user_doc = db.collection("users").document(uid).get()
        user_data = user_doc.to_dict() if user_doc.exists else {}

        sel_doc = db.collection("studentSelections").document(uid).get()
        sel_data = sel_doc.to_dict() if sel_doc.exists else None

        notes_data = [n.to_dict() for n in db.collection("notes").where("uid", "==", uid).stream()]
        txns_data = [t.to_dict() for t in db.collection("transactions").where("uid", "==", uid).stream()]
        chats_data = []
        for c in db.collection("ai_chat_sessions").where("student_id", "==", uid).stream():
            chats_data.append(c.to_dict() | {"session_id": c.id})
        for c in db.collection("ai_chat_sessions").where("uid", "==", uid).stream():
            if not any(x.get("session_id") == c.id for x in chats_data):
                chats_data.append(c.to_dict() | {"session_id": c.id})

        email = user_data.get("email") or (sel_data.get("email") if sel_data else "") or ""
        name = user_data.get("name") or (sel_data.get("name") if sel_data else "Student")
        archived_at = datetime.now(timezone.utc).isoformat()
        admin_email = admin_user.get("email") or "admin"

        # 1. Archive full snapshot into separate storage/collection
        archive_payload = {
            "uid": uid,
            "email": email,
            "name": name,
            "status": "banned_and_archived",
            "reason": "Banned and deleted by administrator",
            "archived_at": archived_at,
            "banned_by": admin_email,
            "user_data": user_data,
            "student_selections": sel_data,
            "notes_count": len(notes_data),
            "transactions_count": len(txns_data),
            "chats_count": len(chats_data),
            "notes": notes_data,
            "transactions": txns_data,
            "chats": chats_data,
        }
        db.collection("deleted_students_archive").document(uid).set(archive_payload)

        # 2. Add to banned_students registry
        ban_record = {
            "uid": uid,
            "email": email.lower() if email else "",
            "name": name,
            "banned_at": archived_at,
            "banned_by": admin_email,
            "reason": "Banned and deleted by administrator",
            "status": "banned",
        }
        db.collection("banned_students").document(uid).set(ban_record)
        if email:
            db.collection("banned_students").document(f"email_{email.lower()}").set(ban_record)

        # 3. Revoke active JWT sessions / token versions
        try:
            from app.services.device_service import revoke_user_sessions
            await revoke_user_sessions(uid)
        except Exception:
            pass

        # 4. Attempt Firebase Auth deletion if available
        try:
            from firebase_admin import auth as fb_auth
            fb_auth.delete_user(uid)
        except Exception:
            pass

        # 5. Purge from active operational collections
        db.collection("users").document(uid).delete()
        db.collection("studentSelections").document(uid).delete()
        db.collection("refreshTokens").document(uid).delete()
        for n in db.collection("notes").where("uid", "==", uid).stream():
            n.reference.delete()
        for t in db.collection("transactions").where("uid", "==", uid).stream():
            t.reference.delete()
        for c in db.collection("ai_chat_sessions").where("student_id", "==", uid).stream():
            c.reference.delete()
        for c in db.collection("ai_chat_sessions").where("uid", "==", uid).stream():
            c.reference.delete()
        for d in db.collection("device_bindings").where("uid", "==", uid).stream():
            d.reference.delete()

        return {
            "success": True,
            "uid": uid,
            "status": "banned_and_archived",
            "archived_at": archived_at,
            "message": "Student has been banned, all active data purged, and record archived separately."
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



# ─── AI Chat Sessions ─────────────────────────────────────────────────────────

@router.get("/ai-chats")
async def list_ai_chat_sessions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    uid: Optional[str] = Query(None),
    flagged_only: Optional[bool] = Query(False),
    admin_user: Dict[str, Any] = Depends(require_admin_user),
):
    """Browse all AI chat sessions saved in Firestore with safety moderation status."""
    try:
        from app.services.moderation import analyze_content_safety
        ref = db.collection("ai_chat_sessions")
        if uid:
            raw_docs = []
            for d in ref.stream():
                item = d.to_dict() | {"session_id": d.id}
                if item.get("uid") == uid or item.get("student_id") == uid:
                    raw_docs.append(item)
        else:
            raw_docs = [d.to_dict() | {"session_id": d.id} for d in ref.stream()]

        # Dynamically inspect and ensure flagged status is accurate for every session
        analyzed_docs = []
        for s in raw_docs:
            is_flagged = bool(s.get("is_flagged", False))
            flag_reasons = set(s.get("flag_reasons", []))
            flagged_terms = set(s.get("flagged_terms", []))

            # Inspect messages to guarantee zero misses
            for m in s.get("messages", []):
                if m.get("role") == "user":
                    content = m.get("content", "")
                    mod = analyze_content_safety(content)
                    if mod["is_flagged"]:
                        is_flagged = True
                        flag_reasons.update(mod["flag_reasons"])
                        flagged_terms.update(mod["flagged_terms"])

            s["is_flagged"] = is_flagged
            s["flag_reasons"] = sorted(list(flag_reasons))
            s["flagged_terms"] = sorted(list(flagged_terms))
            analyzed_docs.append(s)

        # Sort: flagged sessions first, then most recently updated
        analyzed_docs.sort(key=lambda x: (1 if x.get("is_flagged") else 0, x.get("updated_at", "")), reverse=True)

        flagged_count = sum(1 for s in analyzed_docs if s.get("is_flagged"))

        if flagged_only:
            filtered = [s for s in analyzed_docs if s.get("is_flagged")]
        else:
            filtered = analyzed_docs

        total = len(filtered)
        start = (page - 1) * page_size
        return {
            "sessions": filtered[start: start + page_size],
            "total": total,
            "flagged_count": flagged_count,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ai-chats/{session_id}")
async def get_ai_chat_session(session_id: str, admin_user: Dict[str, Any] = Depends(require_admin_user)):
    """Full message history for a specific AI chat session with per-message moderation info."""
    try:
        from app.services.moderation import analyze_content_safety
        doc = db.collection("ai_chat_sessions").document(session_id).get()
        if not doc.exists:
            raise HTTPException(status_code=404, detail="Session not found")
        data = doc.to_dict()
        messages = data.get("messages", [])

        # Enrich messages with safety flags if user prompt is flagged
        for m in messages:
            if m.get("role") == "user":
                mod = analyze_content_safety(m.get("content", ""))
                if mod["is_flagged"]:
                    m["is_flagged"] = True
                    m["flag_reasons"] = mod["flag_reasons"]
                    m["flagged_terms"] = mod["flagged_terms"]

        is_flagged = data.get("is_flagged", False) or any(m.get("is_flagged") for m in messages)
        flag_reasons = set(data.get("flag_reasons", []))
        flagged_terms = set(data.get("flagged_terms", []))
        for m in messages:
            for r in m.get("flag_reasons", []):
                flag_reasons.add(r)
            for t in m.get("flagged_terms", []):
                flagged_terms.add(t)

        return {
            "messages": messages,
            "is_flagged": is_flagged,
            "flag_reasons": sorted(list(flag_reasons)),
            "flagged_terms": sorted(list(flagged_terms)),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/ai-chats/{session_id}")
async def delete_ai_chat_session(session_id: str, admin_user: Dict[str, Any] = Depends(require_admin_user)):
    """Deletes an AI chat session from Firestore."""
    try:
        db.collection("ai_chat_sessions").document(session_id).delete()
        return {"success": True, "session_id": session_id, "deleted": True}
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

    selections = list(db.collection("studentSelections").stream())
    per_student_percent: List[float] = []

    for s_doc in selections:
        s_data = s_doc.to_dict()
        cid = s_data.get("courseId") or s_data.get("course_id")
        visited = s_data.get("visitedParts") or []
        prefix = f"{course_id}::"
        curriculum_part_ids = {p["part_id"] for p in parts} if parts else set()
        completed = sum(1 for v in visited if isinstance(v, str) and (v.startswith(prefix) or v in curriculum_part_ids))

        if cid == course_id or completed > 0:
            if total_parts > 0:
                pct = min(100.0, round((completed / total_parts) * 100.0, 1))
            else:
                pct = 0.0
            per_student_percent.append(pct)

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
        selections = {d.id: d.to_dict() for d in db.collection("studentSelections").stream()}
        users = {d.id: d.to_dict() for d in db.collection("users").stream()}

        rows: List[Dict[str, Any]] = []
        for uid, s_data in selections.items():
            if uid.startswith("student_test"):
                continue
            cid = s_data.get("courseId") or s_data.get("course_id")
            visited = s_data.get("visitedParts") or []
            u_data = users.get(uid, {})
            name = u_data.get("name") or u_data.get("displayName") or "Student"

            course_title = courses.get(cid, s_data.get("courseName") or "Enrolled Course")
            parts = await db.get_course_curriculum(cid) if cid else []
            total_parts = len(parts) if parts else (len(visited) or 1)
            completed_parts = len(visited)
            percent = min(100.0, round((completed_parts / total_parts) * 100.0, 1)) if total_parts else 0.0

            rows.append({
                "uid": uid,
                "student_name": name,
                "course_id": cid or "",
                "course_title": course_title,
                "total_parts": total_parts,
                "completed_parts": completed_parts,
                "completion_percent": percent,
                "last_activity": u_data.get("updated_at") or u_data.get("createdAt") or u_data.get("created_at") or "",
            })

        rows.sort(key=lambda r: (r["completion_percent"], r["completed_parts"]), reverse=True)
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


@router.delete("/practice/quiz/{set_id}")
async def delete_quiz_set(set_id: str, admin_user: Dict[str, Any] = Depends(require_admin_user)):
    """Deletes a quiz set and all its questions."""
    try:
        db.collection("practice_quizzes").document(set_id).delete()
        for q in db.collection("quiz_questions").where("set_id", "==", set_id).stream():
            q.reference.delete()
        return {"success": True, "set_id": set_id, "deleted": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/practice/quiz/{set_id}/questions/{question_id}")
async def delete_quiz_question(set_id: str, question_id: str, admin_user: Dict[str, Any] = Depends(require_admin_user)):
    """Deletes an individual question from a quiz set and decrements question_count."""
    try:
        db.collection("quiz_questions").document(question_id).delete()
        set_ref = db.collection("practice_quizzes").document(set_id)
        set_doc = set_ref.get()
        if set_doc.exists:
            current = max(0, set_doc.to_dict().get("question_count", 1) - 1)
            set_ref.update({"question_count": current})
        return {"success": True, "question_id": question_id, "deleted": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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


# ─── Practice: PYQ admin CRUD ────────────────────────────────────────────────

class PYQCreate(BaseModel):
    board: str
    year: str
    subject: str
    question: str
    solution: str
    marks: int = 0


@router.get("/practice/pyq")
async def list_pyqs(
    board: Optional[str] = Query(None),
    year: Optional[str] = Query(None),
    subject: Optional[str] = Query(None),
    admin_user: Dict[str, Any] = Depends(require_admin_user),
):
    """List admin-curated PYQs with optional filters (board, year, subject)."""
    try:
        ref = db.collection("pyqs")
        if board:
            ref = ref.where("board", "==", board)
        if year:
            ref = ref.where("year", "==", year)
        if subject:
            ref = ref.where("subject", "==", subject)
        rows = [d.to_dict() | {"id": d.id} for d in ref.stream()]
        return {"pyqs": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/practice/pyq")
async def create_pyq(body: PYQCreate, admin_user: Dict[str, Any] = Depends(require_admin_user)):
    pyq_id = f"pyq_{uuid.uuid4().hex[:10]}"
    data = body.model_dump() | {
        "id": pyq_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    db.collection("pyqs").document(pyq_id).set(data)
    return data


@router.delete("/practice/pyq/{pyq_id}")
async def delete_pyq(pyq_id: str, admin_user: Dict[str, Any] = Depends(require_admin_user)):
    db.collection("pyqs").document(pyq_id).delete()
    return {"success": True, "pyq_id": pyq_id, "deleted": True}


# ─── Practice: Predict the Output CRUD ───────────────────────────────────────

class PredictOutputSetCreate(BaseModel):
    title: str
    topic: str = "General"
    difficulty: str = "Easy"  # Easy | Medium | Hard
    question_count: str = "10 Questions"
    code_snippet: str
    expected_output: Optional[str] = None
    set_number: Optional[int] = None
    source: str = "sir"


@router.get("/practice/predict-output")
async def list_predict_output_sets(admin_user: Dict[str, Any] = Depends(require_admin_user)):
    """List all Predict the Output sets from Firestore (and curated baseline if empty)."""
    from app.routers.practice import DEFAULT_SIR_PREDICT
    try:
        sets = [d.to_dict() | {"id": d.id} for d in db.collection("predict_output_sets").stream()]
        if not sets:
            sets = [dict(s) for s in DEFAULT_SIR_PREDICT]
        else:
            existing_ids = {s.get("id") for s in sets}
            for ds in DEFAULT_SIR_PREDICT:
                if ds.get("id") not in existing_ids:
                    sets.append(dict(ds))
        return {"sets": sets}
    except Exception as e:
        logger.warning(f"Error reading predict_output_sets: {e}")
        return {"sets": [dict(s) for s in DEFAULT_SIR_PREDICT]}


@router.post("/practice/predict-output")
async def create_predict_output_set(body: PredictOutputSetCreate, admin_user: Dict[str, Any] = Depends(require_admin_user)):
    from app.routers.practice import invalidate_predict_output_cache
    set_id = f"po_{uuid.uuid4().hex[:10]}"
    data = body.model_dump() | {
        "id": set_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if not data.get("set_number"):
        try:
            existing = list(db.collection("predict_output_sets").stream())
            data["set_number"] = len(existing) + 1
        except Exception:
            data["set_number"] = 1
    db.collection("predict_output_sets").document(set_id).set(data)
    invalidate_predict_output_cache()
    return data


@router.delete("/practice/predict-output/{set_id}")
async def delete_predict_output_set(set_id: str, admin_user: Dict[str, Any] = Depends(require_admin_user)):
    from app.routers.practice import invalidate_predict_output_cache
    db.collection("predict_output_sets").document(set_id).delete()
    invalidate_predict_output_cache()
    return {"success": True, "set_id": set_id, "deleted": True}


# ─── Practice: AI Ingest (text or PDF → AI → save to right collection) ──────
#
# The admin web's "+" button on each practice page offers:
#   - "Write" — a structured text editor
#   - "PDF"   — file upload
# Both paths hit this endpoint. We ask Mistral/Gemini to parse the
# content into the schema of the requested content_type and save the
# resulting records to the right Firestore collection.

class IngestRequest(BaseModel):
    content_type: str  # "quiz" | "coding" | "mcq" | "pyq" | "predict_output"
    subject: str
    title: Optional[str] = None
    text: Optional[str] = None
    pdf_base64: Optional[str] = None
    set_id: Optional[str] = None  # for quiz questions, optional — auto-create if missing
    model: Optional[str] = "mistral"


def _parse_json_block(raw: str) -> Any:
    """Robust JSON extraction from an LLM response (handles ```json fences, prose, etc.)."""
    if not raw:
        raise ValueError("empty model response")
    s = raw.strip()
    # Strip ```json ... ``` fences
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
        s = s.strip()
    # Find the first { or [ and the matching close
    first_obj = s.find("{")
    first_arr = s.find("[")
    candidates = [i for i in (first_obj, first_arr) if i >= 0]
    if not candidates:
        raise ValueError("no JSON object/array found in model response")
    start = min(candidates)
    s = s[start:]
    return json.loads(s)


async def _call_ai_parser(prompt: str, model: str) -> str:
    """Call XKIRO (preferred, OpenAI-compatible), then Mistral or Gemini. Return raw text."""
    from app.core.config import settings
    import httpx

    last_error = None

    # 1. XKIRO – OpenAI compatible
    if settings.XKIRO_API_KEY:
        base = (settings.XKIRO_BASE_URL or "https://api.xkiro.com/v1").rstrip("/")
        if not base.endswith("/v1"):
            base = base + "/v1"
        url = f"{base}/chat/completions"

        candidate_models = []
        if model and ("/" in model or model == "openai/gpt-5.6-sol"):
            candidate_models.append(model)
        candidate_models.extend([
            "qwen/qwen3.5-flash:free",
            "minimax/minimax-m2.5-highspeed:free",
            "minimax/minimax-m2:free",
        ])

        for xkiro_model in candidate_models:
            try:
                async with httpx.AsyncClient(timeout=45.0) as client:
                    r = await client.post(
                        url,
                        headers={"Authorization": f"Bearer {settings.XKIRO_API_KEY}"},
                        json={
                            "model": xkiro_model,
                            "messages": [{"role": "user", "content": prompt}],
                            "temperature": 0.1,
                            "max_tokens": 4000,
                        },
                    )
                    r.raise_for_status()
                    msg = r.json()["choices"][0]["message"]
                    content = msg.get("content") or ""
                    if not content.strip() and msg.get("reasoning_content"):
                        content = msg.get("reasoning_content") or ""
                    if content.strip():
                        return content
            except Exception as e:
                logger.warning(f"XKIRO model '{xkiro_model}' failed: {e}. Trying next model...")
                last_error = f"XKIRO ({xkiro_model}): {e}"
                continue

    if (model == "gemini" or not settings.XKIRO_API_KEY) and settings.GEMINI_API_KEY:
        try:
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"gemini-1.5-flash:generateContent?key={settings.GEMINI_API_KEY}"
            )
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(
                    url,
                    json={
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 4000},
                    },
                )
                r.raise_for_status()
                data = r.json()
                return data["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:
            logger.warning(f"Gemini parser call failed: {e}")
            last_error = f"Gemini: {e}"

    if settings.MISTRAL_API_KEY:
        for m_name in ["mistral-small-latest", "mistral-large-latest", "open-mistral-7b"]:
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    r = await client.post(
                        "https://api.mistral.ai/v1/chat/completions",
                        headers={"Authorization": f"Bearer {settings.MISTRAL_API_KEY}"},
                        json={
                            "model": m_name,
                            "messages": [{"role": "user", "content": prompt}],
                            "temperature": 0.1,
                            "max_tokens": 4000,
                        },
                    )
                    r.raise_for_status()
                    return r.json()["choices"][0]["message"]["content"]
            except Exception as e:
                last_error = f"Mistral ({m_name}): {e}"
                continue

    if last_error:
        raise HTTPException(status_code=502, detail=f"All configured AI providers failed. Last error: {last_error}")

    raise HTTPException(
        status_code=503,
        detail=(
            "No AI model configured. Set XKIRO_API_KEY (recommended, uses minimax/minimax-m2:free) or MISTRAL_API_KEY/GEMINI_API_KEY in backend env to enable ingest."
        ),
    )


def _build_ingest_prompt(content_type: str, subject: str, text: str) -> str:
    """Tell the model exactly which JSON shape to return for each content_type."""
    if content_type == "quiz":
        return (
            f"You are an exam question parser. Given the following text (which may be a single "
            f"question or a set of questions for a quiz titled '{subject}'), extract all multiple-choice "
            f"questions.\n\nReturn a JSON object with this exact shape:\n"
            f'{{"title": "<quiz title>", "questions": [\n'
            f'  {{"question": "...", "options": ["A", "B", "C", "D"], "correct_index": 0, '
            f'"explanation": "...", "difficulty": "easy|medium|hard"}}\n'
            f"]}}\n\nDo not include any other prose — only the JSON object.\n\n---\n{text}\n---"
        )
    if content_type == "mcq":
        return (
            f"You are an exam question parser. Given the following text about '{subject}', extract "
            f"each standalone multiple-choice question.\n\n"
            f'Return a JSON object: {{"questions": [{{"question": "...", "options": ["A", "B", "C", "D"], '
            f'"correct_index": 0, "explanation": "...", "topic": "<sub-topic>", '
            f'"difficulty": "easy|medium|hard"}}]}}.\n'
            f"Do not include any other prose — only the JSON object.\n\n---\n{text}\n---"
        )
    if content_type == "coding":
        return (
            f"You are a coding exercise parser. Given the following text about '{subject}', extract "
            f"each coding exercise.\n\nReturn a JSON object with the key 'exercises' (array). Each item:\n"
            f'  title (string), description (string), language (one of java|python|cpp|javascript|sql), '
            f'starter_code (string), solution_code (string), '
            f'test_cases (array of objects with input and expected_output), '
            f"difficulty (one of easy|medium|hard).\n"
            f"Do not include any other prose — only the JSON object.\n\n---\n{text}\n---"
        )
    if content_type == "pyq":
        return (
            f"You are a past-year question paper parser. Given the following text about '{subject}', "
            f"extract each question and its model answer.\n\n"
            f'Return a JSON object: {{"questions": [\n'
            f'  {{"question": "...", "solution": "...", "marks": <int>}}\n'
            f"]}}\nDo not include any other prose — only the JSON object.\n\n---\n{text}\n---"
        )
    if content_type == "predict_output":
        return (
            f"You are a code tracing and 'predict the output' problem generator/parser. "
            f"Given the following text or topic about '{subject}', extract or generate "
            f"predict-the-output problem sets.\n\n"
            f"Return a JSON object with the key 'sets' (array). Each item must have:\n"
            f"  title (string, e.g. 'Loop Tracing & Conditionals'),\n"
            f"  topic (string, e.g. 'Loops & Control Flow'),\n"
            f"  question_count (string, e.g. '10 Questions'),\n"
            f"  difficulty (one of: 'Easy', 'Medium', 'Hard'),\n"
            f"  code_snippet (valid code snippet to trace in Java or Python),\n"
            f"  expected_output (exact console output string that executing this code produces).\n\n"
            f"Do not include any other prose — only the JSON object.\n\n---\n{text}\n---"
        )
    raise HTTPException(status_code=400, detail=f"Unknown content_type: {content_type}")


@router.post("/practice/ingest")
async def ingest_practice_content(
    body: IngestRequest,
    admin_user: Dict[str, Any] = Depends(require_admin_user),
):
    """
    Ingest practice content from text or PDF, parse via AI, and save to the
    right Firestore collection. Returns the list of created records.

    Body:
      - content_type: "quiz" | "coding" | "mcq" | "pyq"
      - subject: free-text label (board/topic/course)
      - title: optional quiz title (used only for content_type=quiz)
      - text: the raw source text (mutually exclusive with pdf_base64)
      - pdf_base64: the PDF file encoded as base64 (mutually exclusive with text)
      - set_id: optional existing quiz set id; auto-created if absent
      - model: "mistral" (default) or "gemini"
    """
    if not body.text and not body.pdf_base64:
        raise HTTPException(status_code=400, detail="Provide either `text` or `pdf_base64`")
    if body.text and body.pdf_base64:
        raise HTTPException(status_code=400, detail="Provide only one of `text` or `pdf_base64`")
    if body.content_type not in {"quiz", "coding", "mcq", "pyq", "predict_output"}:
        raise HTTPException(status_code=400, detail="content_type must be one of: quiz, coding, mcq, pyq, predict_output")

    # 1. Extract text from PDF if needed.
    source_text = body.text or ""
    if body.pdf_base64:
        try:
            import base64
            import io
            try:
                from pypdf import PdfReader  # type: ignore
            except Exception:
                from PyPDF2 import PdfReader  # type: ignore
            pdf_bytes = base64.b64decode(body.pdf_base64)
            reader = PdfReader(io.BytesIO(pdf_bytes))
            source_text = "\n\n".join((p.extract_text() or "") for p in reader.pages).strip()
            if not source_text:
                raise HTTPException(
                    status_code=400,
                    detail="Could not extract any text from the PDF (image-only or empty).",
                )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to parse PDF: {e}")

    # 2. Ask the AI to extract structured records.
    prompt = _build_ingest_prompt(body.content_type, body.subject, source_text)
    try:
        raw = await _call_ai_parser(prompt, body.model or "mistral")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI parser call failed: {e}")

    # 3. Parse the JSON out of the model response.
    try:
        parsed = _parse_json_block(raw)
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"AI returned unparseable JSON: {e}. Raw (first 200 chars): {raw[:200]}",
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    created_ids: List[str] = []

    # 4. Persist into the right collection.
    if body.content_type == "quiz":
        set_id = body.set_id or str(uuid.uuid4())
        title = body.title or (parsed.get("title") if isinstance(parsed, dict) else None) or body.subject
        # Ensure the quiz set exists.
        set_ref = db.collection("practice_quizzes").document(set_id)
        if not set_ref.get().exists:
            set_ref.set({
                "id": set_id,
                "title": title,
                "subject": body.subject,
                "question_count": 0,
                "created_at": now_iso,
            })
        else:
            # Refresh title if the admin provided one
            if body.title:
                set_ref.update({"title": body.title, "subject": body.subject})

        questions = (parsed.get("questions") if isinstance(parsed, dict) else None) or []
        if not isinstance(questions, list) or not questions:
            raise HTTPException(status_code=400, detail="AI returned no questions")

        for q in questions:
            qid = str(uuid.uuid4())
            qdata = {
                "id": qid,
                "set_id": set_id,
                "question": str(q.get("question", "")).strip(),
                "options": [str(x) for x in (q.get("options") or [])][:6],
                "correct_index": int(q.get("correct_index", 0) or 0),
                "explanation": str(q.get("explanation", "")).strip(),
                "subject": body.subject,
                "difficulty": str(q.get("difficulty", "easy")).lower(),
                "created_at": now_iso,
            }
            db.collection("quiz_questions").document(qid).set(qdata)
            created_ids.append(qid)
        # Update question count
        set_ref.update({"question_count": len(created_ids)})
        return {
            "success": True,
            "content_type": "quiz",
            "set_id": set_id,
            "created_count": len(created_ids),
            "ids": created_ids,
        }

    if body.content_type == "mcq":
        questions = (parsed.get("questions") if isinstance(parsed, dict) else None) or []
        if not isinstance(questions, list) or not questions:
            raise HTTPException(status_code=400, detail="AI returned no questions")
        for q in questions:
            mid = str(uuid.uuid4())
            db.collection("mcqs").document(mid).set({
                "id": mid,
                "question": str(q.get("question", "")).strip(),
                "options": [str(x) for x in (q.get("options") or [])][:6],
                "correct_index": int(q.get("correct_index", 0) or 0),
                "explanation": str(q.get("explanation", "")).strip(),
                "subject": body.subject,
                "topic": str(q.get("topic", body.subject)).strip(),
                "difficulty": str(q.get("difficulty", "easy")).lower(),
                "created_at": now_iso,
            })
            created_ids.append(mid)
        return {
            "success": True,
            "content_type": "mcq",
            "created_count": len(created_ids),
            "ids": created_ids,
        }

    if body.content_type == "coding":
        exercises = (parsed.get("exercises") if isinstance(parsed, dict) else None) or []
        if not isinstance(exercises, list) or not exercises:
            raise HTTPException(status_code=400, detail="AI returned no exercises")
        for ex in exercises:
            eid = str(uuid.uuid4())
            db.collection("coding_exercises").document(eid).set({
                "id": eid,
                "title": str(ex.get("title", "Untitled")).strip(),
                "description": str(ex.get("description", "")).strip(),
                "language": str(ex.get("language", "java")).lower(),
                "starter_code": str(ex.get("starter_code", "")).strip(),
                "solution_code": str(ex.get("solution_code", "")).strip(),
                "test_cases": ex.get("test_cases") or [],
                "difficulty": str(ex.get("difficulty", "easy")).lower(),
                "created_at": now_iso,
            })
            created_ids.append(eid)
        return {
            "success": True,
            "content_type": "coding",
            "created_count": len(created_ids),
            "ids": created_ids,
        }

    if body.content_type == "pyq":
        questions = (parsed.get("questions") if isinstance(parsed, dict) else None) or []
        if not isinstance(questions, list) or not questions:
            raise HTTPException(status_code=400, detail="AI returned no questions")
        for q in questions:
            pyq_id = f"pyq_{uuid.uuid4().hex[:10]}"
            db.collection("pyqs").document(pyq_id).set({
                "id": pyq_id,
                "board": body.subject.split("|")[0].strip() if "|" in body.subject else "ICSE",
                "year": body.subject.split("|")[1].strip() if "|" in body.subject else "2024",
                "subject": body.subject.split("|")[2].strip() if "|" in body.subject else body.subject,
                "question": str(q.get("question", "")).strip(),
                "solution": str(q.get("solution", "")).strip(),
                "marks": int(q.get("marks", 0) or 0),
                "created_at": now_iso,
            })
            created_ids.append(pyq_id)
        return {
            "success": True,
            "content_type": "pyq",
            "created_count": len(created_ids),
            "ids": created_ids,
        }

    if body.content_type == "predict_output":
        from app.routers.practice import invalidate_predict_output_cache
        sets = (parsed.get("sets") if isinstance(parsed, dict) else None) or []
        if not isinstance(sets, list) or not sets:
            sets = (parsed.get("questions") or parsed.get("items") if isinstance(parsed, dict) else None) or []
        if not isinstance(sets, list) or not sets:
            raise HTTPException(status_code=400, detail="AI returned no predict output sets")
        for s in sets:
            sid = f"po_{uuid.uuid4().hex[:10]}"
            doc_data = {
                "id": sid,
                "title": str(s.get("title", body.title or body.subject)).strip(),
                "topic": str(s.get("topic", body.subject)).strip(),
                "question_count": str(s.get("question_count", "10 Questions")).strip(),
                "difficulty": str(s.get("difficulty", "Medium")).capitalize(),
                "code_snippet": str(s.get("code_snippet", "")).strip(),
                "expected_output": str(s.get("expected_output", "")).strip() if s.get("expected_output") else None,
                "source": "sir",
                "created_at": now_iso,
            }
            db.collection("predict_output_sets").document(sid).set(doc_data)
            created_ids.append(sid)
        invalidate_predict_output_cache()
        return {
            "success": True,
            "content_type": "predict_output",
            "created_count": len(created_ids),
            "ids": created_ids,
        }

    raise HTTPException(status_code=500, detail="unreachable")


# ─── Practice: AI Parse Preview (returns parsed JSON without saving) ─────────
# Allows admin to review and edit AI output before committing to Firestore.

async def _extract_source_text(text: Optional[str], pdf_base64: Optional[str]) -> str:
    if text and pdf_base64:
        raise HTTPException(status_code=400, detail="Provide only one of text or pdf_base64")
    if pdf_base64:
        try:
            import base64, io
            try:
                from pypdf import PdfReader  # type: ignore
            except Exception:
                from PyPDF2 import PdfReader  # type: ignore
            pdf_bytes = base64.b64decode(pdf_base64)
            reader = PdfReader(io.BytesIO(pdf_bytes))
            source = "\n\n".join((p.extract_text() or "") for p in reader.pages).strip()
            if not source:
                raise HTTPException(status_code=400, detail="Could not extract any text from the PDF (image-only or empty).")
            return source
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to parse PDF: {e}")
    if text:
        return text.strip()
    raise HTTPException(status_code=400, detail="Provide either text or pdf_base64")


@router.post("/practice/parse")
async def parse_practice_content(
    body: IngestRequest,
    admin_user: Dict[str, Any] = Depends(require_admin_user),
):
    """Parse text/PDF via XKIRO AI and return editable JSON without saving.
    Same payload as /practice/ingest but no DB writes.
    Returns: {success, content_type, parsed: {...}, preview: true}
    """
    if body.content_type not in {"quiz", "coding", "mcq", "pyq", "predict_output"}:
        raise HTTPException(status_code=400, detail="content_type must be one of: quiz, coding, mcq, pyq, predict_output")
    source_text = await _extract_source_text(body.text, body.pdf_base64)
    prompt = _build_ingest_prompt(body.content_type, body.subject, source_text)
    try:
        raw = await _call_ai_parser(prompt, body.model or "minimax/minimax-m2:free")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI parser call failed: {e}")
    try:
        parsed = _parse_json_block(raw)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI returned unparseable JSON: {e}. Raw (first 200 chars): {raw[:200]}")
    return {"success": True, "content_type": body.content_type, "parsed": parsed, "preview": True, "raw_text_preview": source_text[:2000]}


@router.post("/practice/parse/upload")
async def parse_practice_upload(
    content_type: str = Form(...),
    subject: str = Form(...),
    title: Optional[str] = Form(None),
    set_id: Optional[str] = Form(None),
    model: Optional[str] = Form("minimax/minimax-m2:free"),
    file: UploadFile = File(...),
    admin_user: Dict[str, Any] = Depends(require_admin_user),
):
    """Multipart version of /practice/parse for direct PDF upload."""
    if (file.content_type or "").lower() not in {"application/pdf", "application/octet-stream"}:
        raise HTTPException(status_code=400, detail="Only PDF uploads are supported here")
    body_bytes = await file.read()
    import base64
    pdf_b64 = base64.b64encode(body_bytes).decode("ascii")
    fake_req = IngestRequest(content_type=content_type, subject=subject, title=title, pdf_base64=pdf_b64, set_id=set_id, model=model)
    return await parse_practice_content(fake_req, admin_user)


class SaveEditedRequest(BaseModel):
    content_type: str  # quiz | coding | mcq | pyq | predict_output
    subject: str
    title: Optional[str] = None
    set_id: Optional[str] = None
    items: List[Dict[str, Any]]  # edited items from preview


@router.post("/practice/save")
async def save_edited_practice_content(
    body: SaveEditedRequest,
    admin_user: Dict[str, Any] = Depends(require_admin_user),
):
    """Persist admin-edited items from preview. Called after user reviews AI output."""
    if body.content_type not in {"quiz", "coding", "mcq", "pyq", "predict_output"}:
        raise HTTPException(status_code=400, detail="content_type must be one of: quiz, coding, mcq, pyq, predict_output")
    if not body.items:
        raise HTTPException(status_code=400, detail="No items to save")
    now_iso = datetime.now(timezone.utc).isoformat()
    created_ids: List[str] = []
    if body.content_type == "quiz":
        set_id = body.set_id or str(uuid.uuid4())
        title = body.title or body.subject
        set_ref = db.collection("practice_quizzes").document(set_id)
        if not set_ref.get().exists:
            set_ref.set({"id": set_id, "title": title, "subject": body.subject, "question_count": 0, "created_at": now_iso})
        elif body.title:
            set_ref.update({"title": body.title, "subject": body.subject})
        for q in body.items:
            qid = str(uuid.uuid4())
            db.collection("quiz_questions").document(qid).set({
                "id": qid, "set_id": set_id,
                "question": str(q.get("question","")).strip(),
                "options": [str(x) for x in (q.get("options") or [])][:6],
                "correct_index": int(q.get("correct_index",0) or 0),
                "explanation": str(q.get("explanation","")).strip(),
                "subject": body.subject,
                "difficulty": str(q.get("difficulty","easy")).lower(),
                "created_at": now_iso,
            })
            created_ids.append(qid)
        set_ref.update({"question_count": len(created_ids)})
        return {"success": True, "content_type": "quiz", "set_id": set_id, "created_count": len(created_ids), "ids": created_ids}
    if body.content_type == "mcq":
        for q in body.items:
            mid = str(uuid.uuid4())
            db.collection("mcqs").document(mid).set({
                "id": mid, "question": str(q.get("question","")).strip(),
                "options": [str(x) for x in (q.get("options") or [])][:6],
                "correct_index": int(q.get("correct_index",0) or 0),
                "explanation": str(q.get("explanation","")).strip(),
                "subject": body.subject, "topic": str(q.get("topic", body.subject)).strip(),
                "difficulty": str(q.get("difficulty","easy")).lower(), "created_at": now_iso,
            })
            created_ids.append(mid)
        return {"success": True, "content_type": "mcq", "created_count": len(created_ids), "ids": created_ids}
    if body.content_type == "coding":
        for ex in body.items:
            eid = str(uuid.uuid4())
            db.collection("coding_exercises").document(eid).set({
                "id": eid, "title": str(ex.get("title","Untitled")).strip(),
                "description": str(ex.get("description","")).strip(),
                "language": str(ex.get("language","java")).lower(),
                "starter_code": str(ex.get("starter_code","")).strip(),
                "solution_code": str(ex.get("solution_code","")).strip(),
                "test_cases": ex.get("test_cases") or [],
                "difficulty": str(ex.get("difficulty","easy")).lower(), "created_at": now_iso,
            })
            created_ids.append(eid)
        return {"success": True, "content_type": "coding", "created_count": len(created_ids), "ids": created_ids}
    if body.content_type == "pyq":
        for q in body.items:
            pyq_id = f"pyq_{uuid.uuid4().hex[:10]}"
            db.collection("pyqs").document(pyq_id).set({
                "id": pyq_id,
                "board": body.subject.split("|")[0].strip() if "|" in body.subject else str(q.get("board","ICSE")).strip(),
                "year": body.subject.split("|")[1].strip() if "|" in body.subject else str(q.get("year","2024")).strip(),
                "subject": body.subject.split("|")[2].strip() if "|" in body.subject else body.subject,
                "question": str(q.get("question","")).strip(),
                "solution": str(q.get("solution","")).strip(),
                "marks": int(q.get("marks",0) or 0), "created_at": now_iso,
            })
            created_ids.append(pyq_id)
        return {"success": True, "content_type": "pyq", "created_count": len(created_ids), "ids": created_ids}
    if body.content_type == "predict_output":
        from app.routers.practice import invalidate_predict_output_cache
        for s in body.items:
            sid = s.get("id") or f"po_{uuid.uuid4().hex[:10]}"
            db.collection("predict_output_sets").document(sid).set({
                "id": sid,
                "title": str(s.get("title", body.title or body.subject)).strip(),
                "topic": str(s.get("topic", body.subject)).strip(),
                "question_count": str(s.get("question_count", "10 Questions")).strip(),
                "difficulty": str(s.get("difficulty", "Medium")).capitalize(),
                "code_snippet": str(s.get("code_snippet", "")).strip(),
                "expected_output": str(s.get("expected_output", "")).strip() if s.get("expected_output") else None,
                "source": s.get("source", "sir"),
                "created_at": now_iso,
            })
            created_ids.append(sid)
        invalidate_predict_output_cache()
        return {"success": True, "content_type": "predict_output", "created_count": len(created_ids), "ids": created_ids}
    raise HTTPException(status_code=500, detail="unreachable")


# ─── Practice: AI Ingest (multipart, PDF file upload) ────────────────────────
#
# Convenience endpoint so the admin web can upload a PDF directly with
# `FormData` instead of having to base64-encode it client-side. Internally
# it re-uses the same JSON ingest flow by base64-encoding the file.

@router.post("/practice/ingest/upload")
async def ingest_practice_upload(
    content_type: str = Form(...),
    subject: str = Form(...),
    title: Optional[str] = Form(None),
    set_id: Optional[str] = Form(None),
    model: Optional[str] = Form("mistral"),
    file: UploadFile = File(...),
    admin_user: Dict[str, Any] = Depends(require_admin_user),
):
    """Same as /practice/ingest but accepts a multipart PDF upload."""
    if (file.content_type or "").lower() not in {"application/pdf", "application/octet-stream"}:
        raise HTTPException(status_code=400, detail="Only PDF uploads are supported here")
    body_bytes = await file.read()
    import base64
    pdf_b64 = base64.b64encode(body_bytes).decode("ascii")
    # Re-use the JSON ingest path
    from fastapi import Request
    fake_req = IngestRequest(
        content_type=content_type,
        subject=subject,
        title=title,
        pdf_base64=pdf_b64,
        set_id=set_id,
        model=model,
    )
    return await ingest_practice_content(fake_req, admin_user)


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

    # Ensure clean 11-character video ID is resolved
    extracted_id = (body.youtube_video_id or "").strip()
    if len(extracted_id) != 11:
        match = re.search(
            r"[?&]v=([A-Za-z0-9_-]{11})|youtu\.be/([A-Za-z0-9_-]{11})|youtube\.com/(?:shorts|embed|live)/([A-Za-z0-9_-]{11})",
            body.youtube_url or extracted_id
        )
        if match:
            extracted_id = next(g for g in match.groups() if g is not None)

    data = body.model_dump() | {
        "id": vid_id,
        "youtube_video_id": extracted_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        # Populate camelCase fields for direct Android Firestore stream listeners
        "youtubeUrl": body.youtube_url,
        "youtubeVideoId": extracted_id,
        "durationSec": body.duration_sec,
        "videoFormat": body.video_type,
        "whiteboardImageUrl": body.whiteboard_image_url or "",
        "codeSample": body.code_sample or "",
        "isPremium": body.is_premium,
        "isPublished": True,
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


@router.get("/courses")
async def list_admin_courses(admin_user: Dict[str, Any] = Depends(require_admin_user)):
    """Lists all courses for admin curriculum overview."""
    courses = []
    try:
        for doc in db.collection("courses").stream():
            d = doc.to_dict()
            courses.append({
                "id": doc.id,
                "title": d.get("title", "Untitled Course"),
                "description": d.get("description", ""),
                "icon_name": d.get("iconName") or d.get("icon_name", "code"),
                "color": int(d.get("color", 0xFFE65100)),
                "order": int(d.get("order", 1)),
                "is_published": d.get("is_published", True),
            })
        courses.sort(key=lambda x: x["order"])
    except Exception as e:
        logger.warning(f"Error fetching admin courses: {e}")
    return {"courses": courses}


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
    # Invalidate public catalog cache so it reflects immediately in Android app and web
    try:
        from app.routers.catalog import invalidate_catalog_cache
        invalidate_catalog_cache()
    except Exception:
        pass
    return {"success": True, "course": data}


@router.delete("/courses/{course_id}")
async def delete_course(course_id: str, admin_user: Dict[str, Any] = Depends(require_admin_user)):
    """Deletes a course and flushes catalog cache."""
    try:
        db.collection("courses").document(course_id).delete()
        try:
            from app.routers.catalog import invalidate_catalog_cache
            invalidate_catalog_cache()
        except Exception:
            pass
        return {"success": True, "course_id": course_id, "deleted": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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

