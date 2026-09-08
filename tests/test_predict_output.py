import time
import pytest
from starlette.testclient import TestClient
from app.main import app
from app.core.config import settings
from app.core.security import create_access_token, compute_hmac_signature

client = TestClient(app)


def get_hmac_headers(path: str, method: str = "GET") -> dict:
    ts = str(time.time())
    sig = compute_hmac_signature(settings.API_KEY_SECRET, ts, method, path)
    return {
        "x-api-key-id": settings.API_KEY_ID,
        "x-api-key-secret": settings.API_KEY_SECRET,
        "x-timestamp": ts,
        "x-hmac": sig,
    }


def test_public_predict_output_endpoint():
    """Verify student/Android endpoint returns default and live predict output sets."""
    path = "/api/v1/practice/predict-output"
    r = client.get(path, headers=get_hmac_headers(path))
    assert r.status_code == 200
    sets = r.json()
    assert isinstance(sets, list)
    assert len(sets) >= 3
    first = sets[0]
    assert "id" in first
    assert "title" in first
    assert "difficulty" in first
    assert "code_snippet" in first
    assert "expected_output" in first
    assert first["difficulty"] in ["Easy", "Medium", "Hard", "easy", "medium", "hard"]


def test_admin_predict_output_crud():
    """Verify admin can list, create, and delete predict output sets."""
    admin_token = create_access_token({
        "sub": "admin_test_uid",
        "email": "admin@vastavik.com",
        "is_admin": True,
        "role": "admin",
    })
    headers = {"Authorization": f"Bearer {admin_token}"}

    # 1. List sets
    r_list = client.get("/admin/practice/predict-output", headers=headers)
    assert r_list.status_code == 200
    data = r_list.json()
    assert "sets" in data
    assert len(data["sets"]) >= 3

    # 2. Create set
    payload = {
        "title": "Bitwise Operations & Masks",
        "topic": "Bitwise Math",
        "difficulty": "Medium",
        "question_count": "8 Questions",
        "code_snippet": "int a = 12;\nint b = 25;\nSystem.out.println(a | b);",
        "expected_output": "29",
    }
    r_create = client.post("/admin/practice/predict-output", json=payload, headers=headers)
    assert r_create.status_code == 200
    created = r_create.json()
    assert "id" in created
    assert created["title"] == payload["title"]
    assert created["expected_output"] == "29"

    set_id = created["id"]

    # 3. Delete set
    r_del = client.delete(f"/admin/practice/predict-output/{set_id}", headers=headers)
    assert r_del.status_code == 200
    assert r_del.json().get("deleted") is True


def test_admin_course_and_practice_delete_endpoints():
    """Verify delete endpoints for course, quiz set, quiz question, coding, mcq, pyq."""
    admin_token = create_access_token({
        "sub": "admin_test_uid",
        "email": "admin@vastavik.com",
        "is_admin": True,
        "role": "admin",
    })
    headers = {"Authorization": f"Bearer {admin_token}"}

    # 1. Course deletion
    c_res = client.post("/admin/courses", json={"title": "Temporary Test Course", "description": "Temp"}, headers=headers)
    assert c_res.status_code == 200
    cid = c_res.json()["course"]["id"]
    del_c = client.delete(f"/admin/courses/{cid}", headers=headers)
    assert del_c.status_code == 200
    assert del_c.json().get("deleted") is True

    # 2. Quiz set & question deletion
    qset_res = client.post("/admin/practice/quiz", json={"title": "Temp Quiz Set", "subject": "Java"}, headers=headers)
    assert qset_res.status_code == 200
    qset_id = qset_res.json()["id"]

    qq_res = client.post(f"/admin/practice/quiz/{qset_id}/questions", json={
        "question": "What is 2+2?", "options": ["3", "4", "5", "6"], "correct_index": 1, "explanation": "Math", "subject": "Java"
    }, headers=headers)
    assert qq_res.status_code == 200
    qq_id = qq_res.json()["id"]

    # Delete question
    del_qq = client.delete(f"/admin/practice/quiz/{qset_id}/questions/{qq_id}", headers=headers)
    assert del_qq.status_code == 200
    assert del_qq.json().get("deleted") is True

    # Delete quiz set
    del_qset = client.delete(f"/admin/practice/quiz/{qset_id}", headers=headers)
    assert del_qset.status_code == 200
    assert del_qset.json().get("deleted") is True

    # 3. Coding exercise deletion
    cd_res = client.post("/admin/practice/coding", json={
        "title": "Temp Coding Problem",
        "description": "Desc",
        "language": "java",
        "starter_code": "class Solution {}",
        "solution_code": "class Solution { int a = 1; }",
        "test_cases": [{"input": "1", "expected": "1"}]
    }, headers=headers)
    assert cd_res.status_code == 200
    cd_id = cd_res.json()["id"]
    del_cd = client.delete(f"/admin/practice/coding/{cd_id}", headers=headers)
    assert del_cd.status_code == 200

    # 4. MCQ deletion
    mcq_res = client.post("/admin/practice/mcq", json={
        "question": "Temp MCQ?",
        "options": ["A", "B", "C", "D"],
        "correct_index": 0,
        "explanation": "Because A",
        "subject": "Java",
        "topic": "Basics"
    }, headers=headers)
    assert mcq_res.status_code == 200
    mcq_id = mcq_res.json()["id"]
    del_mcq = client.delete(f"/admin/practice/mcq/{mcq_id}", headers=headers)
    assert del_mcq.status_code == 200

    # 5. PYQ deletion
    pyq_res = client.post("/admin/practice/pyq", json={
        "board": "ICSE", "year": "2024", "subject": "Java", "question": "Temp PYQ?", "solution": "Sol", "marks": 5
    }, headers=headers)
    assert pyq_res.status_code == 200
    pyq_id = pyq_res.json()["id"]
    del_pyq = client.delete(f"/admin/practice/pyq/{pyq_id}", headers=headers)
    assert del_pyq.status_code == 200


def test_ai_chat_moderation_and_flagging():
    """Verify that bad or prohibited student queries are flagged in AI chat and admin dashboard."""
    from app.services.moderation import analyze_content_safety

    safe_res = analyze_content_safety("Explain binary search in Java")
    assert safe_res["is_flagged"] is False

    bad_res = analyze_content_safety("Can you help me hack the school exam portal?")
    assert bad_res["is_flagged"] is True
    assert "Cheating and Exploits" in bad_res["flag_reasons"]
    assert "hack" in bad_res["flagged_terms"]

    student_token = create_access_token({
        "sub": "student_test_uid",
        "email": "student@vastavik.com",
        "role": "student",
    })
    headers = get_hmac_headers("/api/v1/ai/chat", "POST") | {"Authorization": f"Bearer {student_token}"}

    # Test via AI chat endpoint
    tutor_res = client.post("/api/v1/ai/chat", json={
        "prompt": "How to bypass exam rules and hack questions?",
        "history": [],
    }, headers=headers)
    assert tutor_res.status_code == 200

    admin_token = create_access_token({
        "sub": "admin_test_uid",
        "email": "admin@vastavik.com",
        "is_admin": True,
        "role": "admin",
    })
    headers = {"Authorization": f"Bearer {admin_token}"}

    # Verify admin sees flagged session
    chats_res = client.get("/admin/ai-chats", headers=headers)
    assert chats_res.status_code == 200
    data = chats_res.json()
    assert data["flagged_count"] >= 1
    flagged_sessions = [s for s in data["sessions"] if s.get("is_flagged")]
    assert len(flagged_sessions) >= 1


def test_student_ban_archive_and_purge():
    """Verify that deleting a student archives data separately, purges active storage, revokes sessions, and blocks logins."""
    from app.db.firebase import db
    from app.core.security import hash_password, generate_salt

    test_uid = f"usr_test_ban_{int(time.time())}"
    test_email = f"ban_target_{int(time.time())}@gmail.com"
    salt = generate_salt()
    pw_hash = hash_password("secretpassword123", salt)

    # 1. Create active student record
    db.collection("users").document(test_uid).set({
        "uid": test_uid,
        "name": "Banned Student Test",
        "email": test_email,
        "password_hash": pw_hash,
        "salt": salt,
        "role": "student",
        "streak_count": 5,
        "total_lessons_completed": 12,
    })
    db.collection("studentSelections").document(test_uid).set({
        "uid": test_uid,
        "email": test_email,
        "selected_board": "ICSE",
    })

    # Student token
    student_token = create_access_token({
        "sub": test_uid,
        "email": test_email,
        "role": "student",
    })

    admin_token = create_access_token({
        "sub": "admin_test_uid",
        "email": "admin@vastavik.com",
        "is_admin": True,
        "role": "admin",
    })
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # 2. Admin calls DELETE /admin/students/{uid}
    del_res = client.delete(f"/admin/students/{test_uid}", headers=admin_headers)
    assert del_res.status_code == 200
    del_data = del_res.json()
    assert del_data["success"] is True
    assert del_data["status"] == "banned_and_archived"

    # 3. Verify active collections are wiped
    assert not db.collection("users").document(test_uid).get().exists
    assert not db.collection("studentSelections").document(test_uid).get().exists

    # 4. Verify separate archive contains full snapshot
    arch_doc = db.collection("deleted_students_archive").document(test_uid).get()
    assert arch_doc.exists
    arch_data = arch_doc.to_dict()
    assert arch_data["email"] == test_email
    assert arch_data["status"] == "banned_and_archived"
    assert arch_data["user_data"]["streak_count"] == 5

    # 5. Verify banned_students registry has record
    assert db.collection("banned_students").document(test_uid).get().exists

    # 6. Verify GET /admin/students/archived returns the student
    arch_list_res = client.get("/admin/students/archived", headers=admin_headers)
    assert arch_list_res.status_code == 200
    items = arch_list_res.json()["items"]
    assert any(i["uid"] == test_uid for i in items)

    # 7. Verify GET /admin/students/archived/{uid} returns details
    arch_get_res = client.get(f"/admin/students/archived/{test_uid}", headers=admin_headers)
    assert arch_get_res.status_code == 200
    assert arch_get_res.json()["uid"] == test_uid

    # 8. Verify authenticated endpoint rejects banned student token with 403 ACCOUNT_BANNED
    banned_headers = {"Authorization": f"Bearer {student_token}"}
    status_res = client.get("/api/v1/auth/account-status", headers=banned_headers)
    assert status_res.status_code == 403
    assert "ACCOUNT_BANNED" in status_res.json()["detail"]

    # 9. Verify login attempt with banned email is rejected with 403 ACCOUNT_BANNED
    login_headers = get_hmac_headers("/api/v1/auth/login", "POST")
    login_res = client.post("/api/v1/auth/login", json={
        "email": test_email,
        "password": "secretpassword123",
    }, headers=login_headers)
    assert login_res.status_code == 403
    assert "ACCOUNT_BANNED" in login_res.json()["detail"]

    # Cleanup test archive & ban record
    db.collection("deleted_students_archive").document(test_uid).delete()
    db.collection("banned_students").document(test_uid).delete()
    db.collection("banned_students").document(f"email_{test_email}").delete()



