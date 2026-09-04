import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from app.core.config import settings

logger = logging.getLogger("vastavik.db")

# Attempt initializing Firebase Admin SDK if credentials exist (file or JSON env)
_firestore_client = None
_firebase_initialized = False

try:
    import firebase_admin
    from firebase_admin import credentials, firestore

    cred = None
    if settings.FIREBASE_CREDENTIALS_JSON:
        cred_dict = json.loads(settings.FIREBASE_CREDENTIALS_JSON)
        cred = credentials.Certificate(cred_dict)
    elif settings.FIREBASE_CREDENTIALS_PATH and os.path.exists(settings.FIREBASE_CREDENTIALS_PATH):
        cred = credentials.Certificate(settings.FIREBASE_CREDENTIALS_PATH)

    if cred:
        # Use existing default app if already initialized
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred, {"projectId": settings.FIREBASE_PROJECT_ID})
        _firestore_client = firestore.client()
        _firebase_initialized = True
        logger.info(f"Firebase Firestore connected successfully to project '{settings.FIREBASE_PROJECT_ID}'.")
except Exception as e:
    logger.warning(f"Could not initialize Firebase Firestore: {e}. Falling back to in-memory store.")


class DatabaseRepository:
    """
    Unified database repository supporting both live Google Firebase Firestore
    and an in-memory repository for zero-setup local development and rapid testing.
    """
    def __init__(self):
        self.use_live_firestore = _firebase_initialized and _firestore_client is not None
        self._memory_users: Dict[str, Dict[str, Any]] = {}
        self._memory_notes: Dict[str, List[Dict[str, Any]]] = {}
        self._memory_progress: Dict[str, List[str]] = {}
        self._memory_transactions: Dict[str, List[Dict[str, Any]]] = {}
        self._memory_doubts: List[Dict[str, Any]] = []
        self._memory_bug_reports: List[Dict[str, Any]] = []
        self._memory_fcm_tokens: Dict[str, str] = {}
        
        # Seed initial catalog data
        self._init_catalog_data()

    def _init_catalog_data(self):
        self._courses = [
            {
                "id": "course_java_icse",
                "title": "Java for ICSE Class 10",
                "description": "Master OOPs, Arrays, Strings, and Class design for ICSE board.",
                "icon_name": "java",
                "color": 0xFF4A90E2,
                "order": 1,
                "is_published": True,
            },
            {
                "id": "course_python_cbse",
                "title": "Python for CBSE Class 12",
                "description": "Functions, File Handling, Data Structures, and SQL Connectivity.",
                "icon_name": "python",
                "color": 0xFF50E3C2,
                "order": 2,
                "is_published": True,
            },
            {
                "id": "course_dsa_foundation",
                "title": "Data Structures & Algorithms",
                "description": "Foundations of recursion, searching, sorting, and binary trees.",
                "icon_name": "binary_tree",
                "color": 0xFFF5A623,
                "order": 3,
                "is_published": True,
            },
        ]
        self._banners = [
            {
                "id": "banner_1",
                "title": "Live Masterclass: ICSE Java Board Revision",
                "image_url": "https://vastaviklearning.com/assets/banner_live.png",
                "target_route": "live_lobby",
            },
            {
                "id": "banner_2",
                "title": "Try Vastavik AI: Code Tutor with zero latency",
                "image_url": "https://vastaviklearning.com/assets/banner_ai.png",
                "target_route": "ai_chat",
            },
        ]
        self._popular_topics = [
            {"id": "topic_1", "name": "Object Oriented Programming", "tag": "Java"},
            {"id": "topic_2", "name": "File Handling in Python", "tag": "Python"},
            {"id": "topic_3", "name": "Recursion & Backtracking", "tag": "DSA"},
            {"id": "topic_4", "name": "SQL Joins and Queries", "tag": "Database"},
        ]
        self._curriculums = {
            "course_java_icse": [
                {
                    "part_id": "part_1",
                    "title": "Introduction to Object-Oriented Concepts",
                    "order": 1,
                    "subparts": [
                        {
                            "subpart_id": "sub_1",
                            "title": "Classes, Objects and Encapsulation",
                            "lesson_id": "lesson_oop_101",
                        },
                        {
                            "subpart_id": "sub_2",
                            "title": "Constructors and Parameterized Methods",
                            "lesson_id": "lesson_const_102",
                        }
                    ]
                },
                {
                    "part_id": "part_2",
                    "title": "Arrays & String Handling",
                    "order": 2,
                    "subparts": [
                        {
                            "subpart_id": "sub_3",
                            "title": "Linear Search and Binary Search in Java",
                            "lesson_id": "lesson_search_201",
                        }
                    ]
                }
            ]
        }
        self._lessons = {
            "lesson_oop_101": {
                "id": "lesson_oop_101",
                "title": "Classes, Objects and Encapsulation",
                "description": "Detailed walk-through of class syntax and state representation.",
                "youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "youtube_video_id": "dQw4w9WgXcQ",
                "duration_sec": 1420,
                "whiteboard_image_url": "https://vastaviklearning.com/notes/oop_wb.png",
                "code_sample": "public class Student {\n    private String name;\n    public Student(String n) { this.name = n; }\n}",
                "notes": "Remember: Encapsulation binds data and functions together into a single unit.",
                "is_premium": False,
                "order": 1,
            },
            "lesson_search_201": {
                "id": "lesson_search_201",
                "title": "Linear Search and Binary Search in Java",
                "description": "Step-by-step ICSE Java implementation of searching algorithms.",
                "youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "youtube_video_id": "dQw4w9WgXcQ",
                "duration_sec": 1800,
                "whiteboard_image_url": "https://vastaviklearning.com/notes/search_wb.png",
                "code_sample": "public static int binarySearch(int[] arr, int target) { ... }",
                "notes": "Binary search precondition: Array MUST be sorted.",
                "is_premium": True,
                "order": 2,
            }
        }
        self._pyqs = [
            {
                "id": "pyq_icse_2023_1",
                "board": "ICSE",
                "year": "2023",
                "subject": "Computer Applications",
                "question": "Define autoboxing and unboxing with examples.",
                "solution": "Autoboxing is the automatic conversion made by Java compiler between primitive types and their corresponding wrapper classes.",
                "marks": 4
            },
            {
                "id": "pyq_cbse_2023_1",
                "board": "CBSE",
                "year": "2023",
                "subject": "Computer Science",
                "question": "Differentiate between append and extend method of list with suitable example.",
                "solution": "append() adds a single element; extend() iterates over its argument adding each element.",
                "marks": 2
            }
        ]

    # --- User Operations ---

    async def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        if self.use_live_firestore:
            users_ref = _firestore_client.collection("users")
            try:
                from google.cloud.firestore_v1.base_query import FieldFilter
                query = users_ref.where(filter=FieldFilter("email", "==", email.lower())).limit(1).stream()
            except Exception:
                query = users_ref.where("email", "==", email.lower()).limit(1).stream()
            for doc in query:
                data = doc.to_dict()
                data["uid"] = doc.id
                return data
            return None
        
        for user in self._memory_users.values():
            if user.get("email", "").lower() == email.lower():
                return user
        return None

    async def get_user_by_id(self, uid: str) -> Optional[Dict[str, Any]]:
        if self.use_live_firestore:
            doc = _firestore_client.collection("users").document(uid).get()
            if doc.exists:
                data = doc.to_dict()
                data["uid"] = doc.id
                return data
            return None
        return self._memory_users.get(uid)

    async def save_user(self, user_data: Dict[str, Any]) -> Dict[str, Any]:
        uid = user_data.get("uid") or str(uuid.uuid4())
        user_data["uid"] = uid
        user_data.setdefault("created_at", datetime.now(timezone.utc).isoformat())

        if self.use_live_firestore:
            _firestore_client.collection("users").document(uid).set(user_data, merge=True)
        else:
            self._memory_users[uid] = user_data
        return user_data

    async def update_user(self, uid: str, updates: Dict[str, Any]) -> bool:
        if self.use_live_firestore:
            _firestore_client.collection("users").document(uid).set(updates, merge=True)
            return True
        else:
            if uid in self._memory_users:
                self._memory_users[uid].update(updates)
                return True
            return False

    # --- Catalog & Curriculum ---

    async def get_home_catalog(self) -> Dict[str, Any]:
        if self.use_live_firestore:
            live_courses = []
            try:
                for doc in _firestore_client.collection("courses").stream():
                    d = doc.to_dict()
                    live_courses.append({
                        "id": doc.id,
                        "title": d.get("title", "Untitled Course"),
                        "description": d.get("description", ""),
                        "icon_name": d.get("iconName") or d.get("icon_name", "code"),
                        "color": int(d.get("color", 0xFF4A90E2)),
                        "order": int(d.get("order", 1)),
                        "is_published": d.get("is_published", True),
                    })
            except Exception as e:
                logger.warning(f"Error querying live courses from Firestore: {e}")

            return {
                "courses": live_courses if live_courses else self._courses,
                "banners": self._banners,
                "popular_topics": self._popular_topics,
            }

        return {
            "courses": self._courses,
            "banners": self._banners,
            "popular_topics": self._popular_topics,
        }

    async def get_course_curriculum(self, course_id: str) -> List[Dict[str, Any]]:
        if self.use_live_firestore:
            try:
                course_ref = _firestore_client.collection("courses").document(course_id)
                parts_stream = course_ref.collection("parts").stream()
                parts = []
                for p_doc in parts_stream:
                    p_data = p_doc.to_dict()
                    part_id = p_doc.id
                    subparts_stream = course_ref.collection("parts").document(part_id).collection("subparts").stream()
                    subparts = []
                    for s_doc in subparts_stream:
                        s_data = s_doc.to_dict()
                        subparts.append({
                            "subpart_id": s_doc.id,
                            "title": s_data.get("title", ""),
                            "lesson_id": s_data.get("lesson_id") or s_doc.id,
                        })
                    parts.append({
                        "part_id": part_id,
                        "title": p_data.get("title", ""),
                        "order": int(p_data.get("order", 1)),
                        "subparts": subparts,
                    })
                if parts:
                    parts.sort(key=lambda x: x["order"])
                    return parts
            except Exception as e:
                logger.warning(f"Error querying live curriculum for course {course_id}: {e}")

        return self._curriculums.get(course_id, [])

    async def get_lesson(self, lesson_id: str) -> Optional[Dict[str, Any]]:
        return self._lessons.get(lesson_id)

    async def mark_visited(self, uid: str, course_id: str, part_id: str) -> bool:
        entry = f"{course_id}::{part_id}"
        if self.use_live_firestore:
            from firebase_admin import firestore
            doc_ref = _firestore_client.collection("studentSelections").document(uid)
            doc_ref.set({"visitedParts": firestore.ArrayUnion([entry])}, merge=True)
            return True
        else:
            if uid not in self._memory_progress:
                self._memory_progress[uid] = []
            if entry not in self._memory_progress[uid]:
                self._memory_progress[uid].append(entry)
            return True

    async def get_visited_parts(self, uid: str) -> List[str]:
        if self.use_live_firestore:
            doc = _firestore_client.collection("studentSelections").document(uid).get()
            if doc.exists:
                return doc.to_dict().get("visitedParts", [])
            return []
        return self._memory_progress.get(uid, [])

    # --- Notes ---

    async def get_notes(self, uid: str) -> List[Dict[str, Any]]:
        if self.use_live_firestore:
            try:
                from google.cloud.firestore_v1.base_query import FieldFilter
                docs = _firestore_client.collection("notes").where(filter=FieldFilter("uid", "==", uid)).stream()
            except Exception:
                docs = _firestore_client.collection("notes").where("uid", "==", uid).stream()
            return [{"id": d.id, **d.to_dict()} for d in docs]
        return self._memory_notes.get(uid, [])

    async def create_note(self, uid: str, note: Dict[str, Any]) -> Dict[str, Any]:
        note_id = str(uuid.uuid4())
        note["id"] = note_id
        note["uid"] = uid
        note["created_at"] = datetime.now(timezone.utc).isoformat()
        if self.use_live_firestore:
            _firestore_client.collection("notes").document(note_id).set(note)
        else:
            if uid not in self._memory_notes:
                self._memory_notes[uid] = []
            self._memory_notes[uid].append(note)
        return note

    async def delete_note(self, uid: str, note_id: str) -> bool:
        if self.use_live_firestore:
            _firestore_client.collection("notes").document(note_id).delete()
            return True
        else:
            if uid in self._memory_notes:
                self._memory_notes[uid] = [n for n in self._memory_notes[uid] if n.get("id") != note_id]
                return True
            return False

    # --- Past Year Questions (PYQs) ---

    async def get_pyqs(self, board: Optional[str] = None, year: Optional[str] = None, subject: Optional[str] = None) -> List[Dict[str, Any]]:
        results = self._pyqs
        if board:
            results = [p for p in results if p.get("board", "").upper() == board.upper()]
        if year:
            results = [p for p in results if str(p.get("year")) == str(year)]
        if subject:
            results = [p for p in results if subject.lower() in p.get("subject", "").lower()]
        return results

    # --- Search ---

    async def search(self, query: str) -> Dict[str, Any]:
        q = query.lower()
        matched_courses = [c for c in self._courses if q in c["title"].lower() or q in c["description"].lower()]
        matched_topics = [t for t in self._popular_topics if q in t["name"].lower() or q in t["tag"].lower()]
        matched_lessons = [l for l in self._lessons.values() if q in l["title"].lower() or q in l["description"].lower()]
        return {
            "query": query,
            "courses": matched_courses,
            "topics": matched_topics,
            "lessons": matched_lessons,
        }

    # --- Payments ---

    async def create_transaction(self, tx: Dict[str, Any]) -> Dict[str, Any]:
        tx_id = tx.get("tx_id") or f"TXN_{uuid.uuid4().hex[:12].upper()}"
        tx["tx_id"] = tx_id
        tx.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        uid = tx.get("uid", "anonymous")
        if self.use_live_firestore:
            _firestore_client.collection("transactions").document(tx_id).set(tx)
        else:
            if uid not in self._memory_transactions:
                self._memory_transactions[uid] = []
            self._memory_transactions[uid].append(tx)
        return tx

    async def get_transactions(self, uid: str) -> List[Dict[str, Any]]:
        if self.use_live_firestore:
            docs = _firestore_client.collection("transactions").where("uid", "==", uid).stream()
            return [{"id": d.id, **d.to_dict()} for d in docs]
        return self._memory_transactions.get(uid, [])

    # --- Doubts & Bug Reports ---

    async def create_doubt(self, doubt: Dict[str, Any]) -> Dict[str, Any]:
        doubt_id = f"DBT-{uuid.uuid4().hex[:8].upper()}"
        doubt["id"] = doubt_id
        doubt["created_at"] = datetime.now(timezone.utc).isoformat()
        if self.use_live_firestore:
            _firestore_client.collection("doubts").document(doubt_id).set(doubt)
        else:
            self._memory_doubts.append(doubt)
        return doubt

    async def create_bug_report(self, report: Dict[str, Any]) -> Dict[str, Any]:
        ticket_id = f"VBUG-{uuid.uuid4().hex[:6].upper()}"
        report["ticket_id"] = ticket_id
        report["created_at"] = datetime.now(timezone.utc).isoformat()
        if self.use_live_firestore:
            _firestore_client.collection("bug_reports").document(ticket_id).set(report)
        else:
            self._memory_bug_reports.append(report)
        return report

    # --- Notifications & FCM ---

    async def save_fcm_token(self, uid: str, token: str) -> bool:
        if self.use_live_firestore:
            _firestore_client.collection("users").document(uid).set({"fcm_token": token}, merge=True)
        else:
            self._memory_fcm_tokens[uid] = token
        return True

    async def get_notifications(self, uid: str) -> List[Dict[str, Any]]:
        # Default starter notifications
        return [
            {
                "id": "notif_welcome",
                "title": "Welcome to Vastavik Learning!",
                "message": "Start your journey with curated ICSE and CBSE programming tracks.",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "is_read": False,
            }
        ]


# Global repository instance
db = DatabaseRepository()
