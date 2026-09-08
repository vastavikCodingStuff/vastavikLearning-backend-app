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
    import base64

    cred = None
    # 1. Base64-encoded credentials (ideal for Render environment variable)
    b64_creds = getattr(settings, "FIREBASE_CREDENTIALS_BASE64", None) or os.environ.get("FIREBASE_CREDENTIALS_BASE64")
    if b64_creds:
        try:
            decoded = base64.b64decode(b64_creds).decode("utf-8")
            cred_dict = json.loads(decoded)
            cred = credentials.Certificate(cred_dict)
            logger.info("Loaded Firebase credentials from FIREBASE_CREDENTIALS_BASE64.")
        except Exception as e:
            logger.warning(f"Failed decoding FIREBASE_CREDENTIALS_BASE64: {e}")

    # 2. JSON string credentials
    if not cred:
        json_creds = getattr(settings, "FIREBASE_CREDENTIALS_JSON", None) or os.environ.get("FIREBASE_CREDENTIALS_JSON")
        if json_creds:
            try:
                cred_dict = json.loads(json_creds)
                cred = credentials.Certificate(cred_dict)
                logger.info("Loaded Firebase credentials from FIREBASE_CREDENTIALS_JSON.")
            except Exception as e:
                logger.warning(f"Failed parsing FIREBASE_CREDENTIALS_JSON: {e}")

    # 3. Render Secret File or standard paths
    if not cred:
        candidate_paths = [
            "/etc/secrets/serviceAccountKey.json",
            "/etc/secrets/service_account.json",
            getattr(settings, "FIREBASE_CREDENTIALS_PATH", None),
            "serviceAccountKey.json",
            os.path.join(os.path.dirname(__file__), "..", "..", "serviceAccountKey.json"),
        ]
        for p in candidate_paths:
            if p and os.path.exists(p):
                try:
                    cred = credentials.Certificate(p)
                    logger.info(f"Loaded Firebase credentials from file: {p}")
                    break
                except Exception as e:
                    logger.warning(f"Failed loading credentials from {p}: {e}")

    if cred:
        # Use existing default app if already initialized
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred, {"projectId": settings.FIREBASE_PROJECT_ID})
        _firestore_client = firestore.client()
        _firebase_initialized = True
        logger.info(f"Firebase Firestore connected successfully to project '{settings.FIREBASE_PROJECT_ID}'.")
    else:
        logger.warning("No Firebase credentials found. Running in in-memory mode.")
except Exception as e:
    logger.warning(f"Could not initialize Firebase Firestore: {e}. Falling back to in-memory store.")


class InMemoryDocSnap:
    def __init__(self, doc_id: str, data: Optional[Dict[str, Any]]):
        self.id = doc_id
        self._data = data
        self.exists = data is not None

    def to_dict(self) -> Dict[str, Any]:
        return self._data.copy() if self._data is not None else {}


class InMemoryDocRef:
    def __init__(self, coll: "InMemoryCollection", doc_id: str):
        self.coll = coll
        self.id = doc_id

    def get(self) -> InMemoryDocSnap:
        data = self.coll._docs.get(self.id)
        return InMemoryDocSnap(self.id, data)

    def set(self, data: Dict[str, Any], merge: bool = False):
        if merge and self.id in self.coll._docs:
            self.coll._docs[self.id].update(data)
        else:
            self.coll._docs[self.id] = data.copy()

    def update(self, data: Dict[str, Any]):
        if self.id in self.coll._docs:
            self.coll._docs[self.id].update(data)
        else:
            self.coll._docs[self.id] = data.copy()

    def delete(self):
        self.coll._docs.pop(self.id, None)

    def collection(self, sub_name: str) -> "InMemoryCollection":
        key = f"{self.coll.name}/{self.id}/{sub_name}"
        return self.coll.repo.collection(key)


class InMemoryCollection:
    def __init__(self, repo: "DatabaseRepository", name: str):
        self.repo = repo
        self.name = name
        if name not in repo._memory_collections:
            repo._memory_collections[name] = {}
        self._docs = repo._memory_collections[name]

    def document(self, doc_id: Optional[str] = None) -> InMemoryDocRef:
        if not doc_id:
            doc_id = str(uuid.uuid4())
        return InMemoryDocRef(self, doc_id)

    def where(self, field: str, op: str, value: Any) -> "InMemoryCollection":
        filtered: Dict[str, Dict[str, Any]] = {}
        for doc_id, data in self._docs.items():
            val = data.get(field)
            match = False
            if op == "==" and val == value:
                match = True
            elif op == "!=" and val != value:
                match = True
            elif op == "in" and isinstance(value, list) and val in value:
                match = True
            if match:
                filtered[doc_id] = data
        new_coll = InMemoryCollection(self.repo, f"{self.name}_filtered_{uuid.uuid4().hex[:6]}")
        new_coll._docs = filtered
        return new_coll

    def stream(self):
        for doc_id, data in list(self._docs.items()):
            yield InMemoryDocSnap(doc_id, data)


class DatabaseRepository:
    """
    Unified database repository supporting both live Google Firebase Firestore
    and an in-memory repository for zero-setup local development and rapid testing.
    """
    def __init__(self):
        self.use_live_firestore = _firebase_initialized and _firestore_client is not None
        self._memory_collections: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._memory_users: Dict[str, Dict[str, Any]] = {}
        self._memory_notes: Dict[str, List[Dict[str, Any]]] = {}
        self._memory_progress: Dict[str, List[str]] = {}
        self._memory_transactions: Dict[str, List[Dict[str, Any]]] = {}
        self._memory_doubts: List[Dict[str, Any]] = []
        self._memory_bug_reports: List[Dict[str, Any]] = []
        self._memory_fcm_tokens: Dict[str, str] = {}
        self._memory_conversations: Dict[str, Dict[str, Any]] = {}
        self._memory_messages: Dict[str, List[Dict[str, Any]]] = {}
        self._memory_activity_logs: Dict[str, List[Dict[str, Any]]] = {}
        
        # Seed initial catalog data
        self._init_catalog_data()

    def collection(self, name: str):
        if self.use_live_firestore and _firestore_client is not None:
            return _firestore_client.collection(name)
        return InMemoryCollection(self, name)

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

        # Seed in-memory collections for admin dashboard and testing
        for c in self._courses:
            self._memory_collections.setdefault("courses", {})[c["id"]] = c

        self._memory_collections.setdefault("users", {})["student_demo_1"] = {
            "uid": "student_demo_1",
            "name": "Arjun Sharma",
            "email": "arjun@example.com",
            "role": "student",
            "preferred_language": "Java",
            "student_class": "Class 10",
            "board": "ICSE",
            "languages": ["Java", "SQL"],
        }
        self._memory_collections.setdefault("users", {})["student_demo_2"] = {
            "uid": "student_demo_2",
            "name": "Priya Patel",
            "email": "priya@example.com",
            "role": "student",
            "preferred_language": "Python",
            "student_class": "Class 12",
            "board": "CBSE",
            "languages": ["Python", "JavaScript"],
        }
        self._memory_collections.setdefault("bug_reports", {})
        self._memory_collections.setdefault("ai_chat_sessions", {})
        self._memory_collections.setdefault("code_executions", {})
        self._memory_collections.setdefault("practice_quizzes", {})
        self._memory_collections.setdefault("quiz_questions", {})
        self._memory_collections.setdefault("coding_exercises", {})
        self._memory_collections.setdefault("mcqs", {})
        self._memory_collections.setdefault("videos", {})

    # --- User Operations ---

    async def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        if self.use_live_firestore:
            try:
                users_ref = _firestore_client.collection("users")
                try:
                    from google.cloud.firestore_v1.base_query import FieldFilter
                    query = users_ref.where(filter=FieldFilter("email", "==", email.lower())).limit(1).stream()
                except Exception:
                    query = users_ref.where("email", "==", email.lower()).limit(1).stream()
                for doc in query:
                    data = doc.to_dict()
                    data["uid"] = doc.id
                    self._memory_users[doc.id] = data
                    return data
            except Exception as e:
                logger.warning(f"Firestore get_user_by_email error: {e}. Falling back to memory.")
        
        for user in self._memory_users.values():
            if user.get("email", "").lower() == email.lower():
                return user
        return None

    async def get_user_by_id(self, uid: str) -> Optional[Dict[str, Any]]:
        if self.use_live_firestore:
            try:
                doc = _firestore_client.collection("users").document(uid).get()
                if doc.exists:
                    data = doc.to_dict()
                    data["uid"] = doc.id
                    self._memory_users[uid] = data
                    return data
            except Exception as e:
                logger.warning(f"Firestore get_user_by_id error: {e}. Falling back to memory.")
        return self._memory_users.get(uid)

    async def save_user(self, user_data: Dict[str, Any]) -> Dict[str, Any]:
        uid = user_data.get("uid") or str(uuid.uuid4())
        user_data["uid"] = uid
        user_data.setdefault("created_at", datetime.now(timezone.utc).isoformat())

        # Dual-write: always write to in-memory store
        self._memory_users[uid] = user_data.copy()

        if self.use_live_firestore:
            try:
                _firestore_client.collection("users").document(uid).set(user_data, merge=True)
            except Exception as e:
                logger.warning(f"Firestore save_user error: {e}. Saved in memory only.")
        return user_data

    async def update_user(self, uid: str, updates: Dict[str, Any]) -> bool:
        # Dual-write: update in-memory store
        if uid not in self._memory_users:
            self._memory_users[uid] = {"uid": uid, "name": "Student", "email": "student@vastavik.com", "role": "student"}
        self._memory_users[uid].update(updates)

        if self.use_live_firestore:
            try:
                _firestore_client.collection("users").document(uid).set(updates, merge=True)
                return True
            except Exception as e:
                logger.warning(f"Firestore update_user error: {e}. Updated in memory only.")
                return True
        return True

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
        if self.use_live_firestore and _firestore_client is not None:
            try:
                # 1. Check flat videos collection (admin uploads)
                v_doc = _firestore_client.collection("videos").document(lesson_id).get()
                if v_doc.exists:
                    d = v_doc.to_dict()
                    d["id"] = v_doc.id
                    return d

                # 2. Check nested curriculum lessons across all courses/parts/subparts
                for l_doc in _firestore_client.collection_group("lessons").stream():
                    d = l_doc.to_dict()
                    if l_doc.id == lesson_id or d.get("id") == lesson_id:
                        d["id"] = l_doc.id
                        return d
            except Exception as e:
                logger.warning(f"Error querying live lesson '{lesson_id}' from Firestore: {e}")

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

    # --- Activity Logs (Android client telemetry) ---

    async def save_activity_log(self, entry: Dict[str, Any]) -> None:
        """Persist a single activity log entry. Best-effort; mirrors to in-memory."""
        uid = entry.get("uid") or "anonymous"
        entry_id = entry.get("id") or str(uuid.uuid4())
        entry["id"] = entry_id
        entry.setdefault("received_at", datetime.now(timezone.utc).isoformat())

        if self.use_live_firestore:
            try:
                _firestore_client.collection("activity_logs").document(entry_id).set(entry, merge=True)
            except Exception as e:
                logger.warning(f"Firestore save_activity_log failed: {e}. Falling back to memory.")

        bucket = self._memory_activity_logs.setdefault(uid, [])
        bucket.append(entry)
        # Cap per-user in-memory log to 1000 entries.
        if len(bucket) > 1000:
            del bucket[: len(bucket) - 1000]

    async def list_activity_logs(self, uid: str, limit: int = 200) -> List[Dict[str, Any]]:
        """List recent activity log entries for a user, newest first."""
        items: List[Dict[str, Any]] = []
        if self.use_live_firestore:
            try:
                docs = (
                    _firestore_client.collection("activity_logs")
                    .where("uid", "==", uid)
                    .limit(limit)
                    .stream()
                )
                for d in docs:
                    payload = d.to_dict() or {}
                    payload["id"] = d.id
                    items.append(payload)
            except Exception as e:
                logger.warning(f"Firestore list_activity_logs failed: {e}. Falling back to memory.")
        if not items:
            items = list(self._memory_activity_logs.get(uid, []))
        # newest first
        items.sort(key=lambda x: x.get("timestamp_ms") or 0, reverse=True)
        return items[:limit]

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

    # ==========================================
    # Conversations & Messages (anytype)
    # ==========================================

    def _conv_doc_ref(self, conv_id: str):
        if self.use_live_firestore:
            return _firestore_client.collection("conversations").document(conv_id)
        return None

    def _msg_col_ref(self, conv_id: str):
        if self.use_live_firestore:
            return _firestore_client.collection("conversations").document(conv_id).collection("messages")
        return None

    async def create_conversation(self, data: Dict[str, Any]) -> Dict[str, Any]:
        conv_id = data.get("id") or f"conv_{uuid.uuid4().hex[:12]}"
        data["id"] = conv_id
        now = datetime.now(timezone.utc).isoformat()
        data.setdefault("created_at", now)
        data.setdefault("updated_at", now)
        data.setdefault("message_count", 0)
        data.setdefault("is_archived", False)
        if self.use_live_firestore:
            try:
                _firestore_client.collection("conversations").document(conv_id).set(data, merge=False)
            except Exception as e:
                logger.warning(f"Firestore create_conversation fallback: {e}")
        # Always mirror to memory for fast fallback reads and index-free queries
        self._memory_conversations[conv_id] = data
        self._memory_messages.setdefault(conv_id, [])
        return data

    async def get_conversation(self, conv_id: str) -> Optional[Dict[str, Any]]:
        if self.use_live_firestore:
            try:
                doc = _firestore_client.collection("conversations").document(conv_id).get()
                if doc.exists:
                    d = doc.to_dict()
                    d["id"] = doc.id
                    return d
            except Exception as e:
                logger.warning(f"Firestore get_conversation error: {e}")
        return self._memory_conversations.get(conv_id)

    async def list_conversations_for_user(self, uid: str, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        if self.use_live_firestore:
            try:
                # Try composite query; if index missing, fall back to simpler query + python sort
                from google.cloud.firestore_v1.base_query import FieldFilter
                try:
                    q = _firestore_client.collection("conversations").where(filter=FieldFilter("participants", "array_contains", uid)).order_by("updated_at", direction="DESCENDING").limit(limit + offset).stream()
                    results = []
                    for doc in q:
                        d = doc.to_dict()
                        d["id"] = doc.id
                        results.append(d)
                    if results:
                        # Merge with in-memory to ensure newly created (mirrored) convs are included if Firestore lags
                        mem_ids = {c["id"] for c in results}
                        for mc in self._memory_conversations.values():
                            if uid in mc.get("participants", []) and mc["id"] not in mem_ids:
                                results.append(mc)
                        results.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
                        return results[offset:offset+limit]
                except Exception as inner:
                    if "index" in str(inner).lower():
                        logger.warning(f"Firestore composite index missing, falling back to simple query: {inner}")
                    # Simple array_contains without ordering (no index needed)
                    q2 = _firestore_client.collection("conversations").where(filter=FieldFilter("participants", "array_contains", uid)).limit(200).stream()
                    results2 = []
                    for doc in q2:
                        d = doc.to_dict()
                        d["id"] = doc.id
                        results2.append(d)
                    # Merge memory
                    mem_ids2 = {c["id"] for c in results2}
                    for mc in self._memory_conversations.values():
                        if uid in mc.get("participants", []) and mc["id"] not in mem_ids2:
                            results2.append(mc)
                    results2.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
                    if results2:
                        return results2[offset:offset+limit]
            except Exception as e:
                logger.warning(f"Firestore list_conversations error: {e}")
        # Fallback in-memory
        all_convs = [c for c in self._memory_conversations.values() if uid in c.get("participants", [])]
        all_convs.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return all_convs[offset: offset + limit]

    async def update_conversation(self, conv_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        if self.use_live_firestore:
            try:
                _firestore_client.collection("conversations").document(conv_id).set(updates, merge=True)
                doc = _firestore_client.collection("conversations").document(conv_id).get()
                if doc.exists:
                    d = doc.to_dict()
                    d["id"] = doc.id
                    return d
            except Exception as e:
                logger.warning(f"Firestore update_conversation error: {e}")
        if conv_id in self._memory_conversations:
            self._memory_conversations[conv_id].update(updates)
            return self._memory_conversations[conv_id]
        return None

    async def add_participants(self, conv_id: str, new_ids: List[str]) -> Optional[Dict[str, Any]]:
        conv = await self.get_conversation(conv_id)
        if not conv:
            return None
        participants = list(set(conv.get("participants", []) + new_ids))
        return await self.update_conversation(conv_id, {"participants": participants})

    async def remove_participant(self, conv_id: str, uid: str) -> Optional[Dict[str, Any]]:
        conv = await self.get_conversation(conv_id)
        if not conv:
            return None
        participants = [p for p in conv.get("participants", []) if p != uid]
        return await self.update_conversation(conv_id, {"participants": participants})

    async def delete_conversation(self, conv_id: str) -> bool:
        if self.use_live_firestore:
            try:
                # Delete subcollection messages (best effort)
                msgs = _firestore_client.collection("conversations").document(conv_id).collection("messages").stream()
                for m in msgs:
                    m.reference.delete()
                _firestore_client.collection("conversations").document(conv_id).delete()
            except Exception as e:
                logger.warning(f"Firestore delete_conversation error: {e}")
        if conv_id in self._memory_conversations:
            del self._memory_conversations[conv_id]
        if conv_id in self._memory_messages:
            del self._memory_messages[conv_id]
        return True

    # --- Messages ---

    async def create_message(self, conv_id: str, msg: Dict[str, Any]) -> Dict[str, Any]:
        msg_id = msg.get("id") or f"msg_{uuid.uuid4().hex[:12]}"
        msg["id"] = msg_id
        msg["conversation_id"] = conv_id
        now = datetime.now(timezone.utc).isoformat()
        msg.setdefault("created_at", now)
        msg.setdefault("reactions", {})
        msg.setdefault("read_by", [])
        if self.use_live_firestore:
            try:
                _firestore_client.collection("conversations").document(conv_id).collection("messages").document(msg_id).set(msg)
                # Update conversation last_message & counters
                _firestore_client.collection("conversations").document(conv_id).set({
                    "last_message": {"id": msg_id, "type": msg.get("type"), "content": msg.get("content","")[:300], "sender_id": msg.get("sender_id"), "created_at": now},
                    "last_message_at": now,
                    "updated_at": now,
                    "message_count": msg.get("_increment", 1),
                }, merge=True)
                # Use transaction increment if needed but simple merge
                try:
                    # Try increment properly
                    from firebase_admin import firestore as fs
                    _firestore_client.collection("conversations").document(conv_id).set({"message_count": fs.Increment(1)}, merge=True)
                except Exception:
                    pass
            except Exception as e:
                logger.warning(f"Firestore create_message error: {e}")
        # Always mirror to memory for fast reads
        if conv_id not in self._memory_messages:
            self._memory_messages[conv_id] = []
        self._memory_messages[conv_id].append(msg)
        # Update memory conversation meta as well
        if conv_id in self._memory_conversations:
            self._memory_conversations[conv_id]["last_message"] = {"id": msg_id, "type": msg.get("type"), "content": msg.get("content","")[:300], "sender_id": msg.get("sender_id"), "created_at": now}
            self._memory_conversations[conv_id]["last_message_at"] = now
            self._memory_conversations[conv_id]["updated_at"] = now
            self._memory_conversations[conv_id]["message_count"] = self._memory_conversations[conv_id].get("message_count", 0) + 1
        return msg

    async def get_messages(self, conv_id: str, limit: int = 50, cursor: Optional[str] = None) -> List[Dict[str, Any]]:
        if self.use_live_firestore:
            try:
                col = _firestore_client.collection("conversations").document(conv_id).collection("messages")
                # Order by created_at desc, handle cursor pagination
                query = col.order_by("created_at", direction="DESCENDING").limit(limit)
                # cursor is iso timestamp or msg id - simple implementation: filter
                docs = list(query.stream())
                msgs = []
                for d in docs:
                    md = d.to_dict()
                    md["id"] = d.id
                    msgs.append(md)
                if msgs:
                    # If cursor provided, find position and slice after
                    if cursor:
                        for idx, m in enumerate(msgs):
                            if m["id"] == cursor or m.get("created_at") == cursor:
                                return msgs[idx+1: idx+1+limit]
                    return msgs
            except Exception as e:
                logger.warning(f"Firestore get_messages error: {e}")
        # Fallback memory: sorted desc
        msgs = self._memory_messages.get(conv_id, [])
        # Sort descending by created_at
        msgs_sorted = sorted(msgs, key=lambda x: x.get("created_at",""), reverse=True)
        if cursor:
            try:
                idx = next(i for i, m in enumerate(msgs_sorted) if m["id"] == cursor or m.get("created_at") == cursor)
                msgs_sorted = msgs_sorted[idx+1:]
            except StopIteration:
                pass
        return msgs_sorted[:limit]

    async def get_message(self, conv_id: str, msg_id: str) -> Optional[Dict[str, Any]]:
        if self.use_live_firestore:
            try:
                doc = _firestore_client.collection("conversations").document(conv_id).collection("messages").document(msg_id).get()
                if doc.exists:
                    d = doc.to_dict()
                    d["id"] = doc.id
                    return d
            except Exception as e:
                logger.warning(f"Firestore get_message error: {e}")
        for m in self._memory_messages.get(conv_id, []):
            if m.get("id") == msg_id:
                return m
        return None

    async def update_message(self, conv_id: str, msg_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        updates["edited_at"] = datetime.now(timezone.utc).isoformat()
        if self.use_live_firestore:
            try:
                _firestore_client.collection("conversations").document(conv_id).collection("messages").document(msg_id).set(updates, merge=True)
            except Exception as e:
                logger.warning(f"Firestore update_message error: {e}")
        for m in self._memory_messages.get(conv_id, []):
            if m.get("id") == msg_id:
                m.update(updates)
                return m
        return None

    async def delete_message(self, conv_id: str, msg_id: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        if self.use_live_firestore:
            try:
                _firestore_client.collection("conversations").document(conv_id).collection("messages").document(msg_id).set({"deleted_at": now, "content": "", "payload": None}, merge=True)
            except Exception as e:
                logger.warning(f"Firestore delete_message error: {e}")
        for m in self._memory_messages.get(conv_id, []):
            if m.get("id") == msg_id:
                m["deleted_at"] = now
                m["content"] = ""
                m["payload"] = None
                return True
        return False

    async def add_reaction(self, conv_id: str, msg_id: str, uid: str, emoji: str) -> Optional[Dict[str, Any]]:
        msg = await self.get_message(conv_id, msg_id)
        if not msg:
            return None
        reactions = msg.get("reactions") or {}
        if emoji not in reactions:
            reactions[emoji] = []
        if uid not in reactions[emoji]:
            reactions[emoji].append(uid)
        return await self.update_message(conv_id, msg_id, {"reactions": reactions})

    async def remove_reaction(self, conv_id: str, msg_id: str, uid: str, emoji: str) -> Optional[Dict[str, Any]]:
        msg = await self.get_message(conv_id, msg_id)
        if not msg:
            return None
        reactions = msg.get("reactions") or {}
        if emoji in reactions and uid in reactions[emoji]:
            reactions[emoji].remove(uid)
            if not reactions[emoji]:
                del reactions[emoji]
        return await self.update_message(conv_id, msg_id, {"reactions": reactions})

    async def mark_read(self, conv_id: str, msg_id: str, uid: str) -> Optional[Dict[str, Any]]:
        msg = await self.get_message(conv_id, msg_id)
        if not msg:
            return None
        read_by = msg.get("read_by") or []
        if uid not in read_by:
            read_by.append(uid)
        return await self.update_message(conv_id, msg_id, {"read_by": read_by})


# Global repository instance
db = DatabaseRepository()
