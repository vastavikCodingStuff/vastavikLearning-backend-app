import time
import uuid
import pytest
from starlette.testclient import TestClient

from app.main import app
from app.core.config import settings
from app.core.security import create_access_token, compute_hmac_signature
from app.db.firebase import db

client = TestClient(app)


def get_hmac_headers(path: str, method: str = "GET", token: str = None) -> dict:
    ts = str(time.time())
    sig = compute_hmac_signature(settings.API_KEY_SECRET, ts, method, path)
    h = {
        "x-api-key-id": settings.API_KEY_ID,
        "x-api-key-secret": settings.API_KEY_SECRET,
        "x-timestamp": ts,
        "x-hmac": sig,
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def test_activity_logging_and_my_history():
    uid = f"student_test_{uuid.uuid4().hex[:6]}"
    token = create_access_token({"sub": uid, "name": "Alice Test", "email": "alice@test.com", "role": "student"})

    path_log = "/api/v1/activity/log"
    payload = {
        "event": "SEARCH",
        "query": "Binary Search",
        "response": "Found 3 lessons",
    }
    headers = get_hmac_headers(path_log, "POST", token)
    res = client.post(path_log, json=payload, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["accepted"] == 1

    path_hist = "/api/v1/activity/my-history"
    headers_hist = get_hmac_headers(path_hist, "GET", token)
    res_hist = client.get(path_hist, headers=headers_hist)
    assert res_hist.status_code == 200
    hist_data = res_hist.json()
    assert hist_data["success"] is True
    assert hist_data["uid"] == uid
    assert any(item.get("query") == "Binary Search" for item in hist_data["items"])


def test_practice_submit_and_history():
    uid = f"student_prac_{uuid.uuid4().hex[:6]}"
    token = create_access_token({"sub": uid, "name": "Bob Test", "email": "bob@test.com", "role": "student"})

    path_submit = "/api/v1/practice/submit"
    mcq_payload = {
        "type": "mcq",
        "topic": "Java Loops",
        "question": "What is the loop output?",
        "options": ["1", "2", "3", "4"],
        "selected_option": 1,
        "correct_option": 1,
        "is_correct": True,
        "verdict": "CORRECT",
        "explanation": "Loop terminates at 2.",
    }
    headers = get_hmac_headers(path_submit, "POST", token)
    res_mcq = client.post(path_submit, json=mcq_payload, headers=headers)
    assert res_mcq.status_code == 200
    assert res_mcq.json()["status"] == "recorded"

    po_payload = {
        "type": "predict_output",
        "topic": "String Operations",
        "code_snippet": "System.out.println(\"Hi\".length());",
        "predicted_output": "2",
        "actual_output": "2",
        "verdict": "CORRECT",
        "is_correct": True,
        "explanation": "Length of 'Hi' is 2.",
        "language": "Java",
    }
    res_po = client.post(path_submit, json=po_payload, headers=headers)
    assert res_po.status_code == 200

    path_hist = "/api/v1/practice/history"
    headers_hist = get_hmac_headers(path_hist, "GET", token)
    res_hist = client.get(path_hist, headers=headers_hist)
    assert res_hist.status_code == 200
    history = res_hist.json()
    assert len(history) >= 2
    types = [h["type"] for h in history]
    assert "mcq" in types
    assert "predict_output" in types


def test_search_tracking_and_history():
    uid = f"student_srch_{uuid.uuid4().hex[:6]}"
    token = create_access_token({"sub": uid, "name": "Charlie", "email": "charlie@test.com", "role": "student"})

    path_search = "/api/v1/search?q=Python+Classes"
    headers = get_hmac_headers("/api/v1/search", "GET", token)
    res = client.get(path_search, headers=headers)
    assert res.status_code == 200

    path_hist = "/api/v1/search/history"
    headers_hist = get_hmac_headers(path_hist, "GET", token)
    res_hist = client.get(path_hist, headers=headers_hist)
    assert res_hist.status_code == 200
    searches = res_hist.json()
    assert any(s.get("query") == "Python Classes" for s in searches)


def test_admin_student_detail_includes_activities_and_practice():
    uid = f"student_adm_{uuid.uuid4().hex[:6]}"
    user_token = create_access_token({"sub": uid, "name": "Dana", "email": "dana@test.com", "role": "student"})
    admin_token = create_access_token({"sub": "admin_master", "name": "Admin", "email": "admin@vastavik.com", "role": "admin"})

    db.collection("users").document(uid).set({
        "uid": uid,
        "name": "Dana",
        "email": "dana@test.com",
        "role": "student",
    })

    path_submit = "/api/v1/practice/submit"
    headers_user = get_hmac_headers(path_submit, "POST", user_token)
    client.post(path_submit, json={
        "type": "coding",
        "problem_title": "Palindrome Check",
        "language": "Java",
        "solution_code": "return str.equals(new StringBuilder(str).reverse().toString());",
        "verdict": "SOLVED",
        "is_correct": True,
    }, headers=headers_user)

    path_student = f"/admin/students/{uid}"
    headers_admin = get_hmac_headers(path_student, "GET", admin_token)
    res_adm = client.get(path_student, headers=headers_admin)
    assert res_adm.status_code == 200
    student_data = res_adm.json()
    assert "activities" in student_data
    assert "practice_history" in student_data
    assert any(p.get("problem_title") == "Palindrome Check" for p in student_data["practice_history"])


def test_user_activity_log_authorization_and_spoofing():
    alice_uid = f"alice_{uuid.uuid4().hex[:6]}"
    bob_uid = f"bob_{uuid.uuid4().hex[:6]}"
    alice_token = create_access_token({"sub": alice_uid, "name": "Alice", "role": "student"})
    bob_token = create_access_token({"sub": bob_uid, "name": "Bob", "role": "student"})
    admin_token = create_access_token({"sub": "admin_user", "name": "Admin", "role": "admin"})

    # Ingest log for Alice, but attempt to spoof Bob's UID
    log_path = "/api/v1/activity/log"
    spoofed_payload = {
        "event": "CHEAT_ATTEMPT",
        "uid": bob_uid,  # Alice tries to impersonate Bob
        "query": "malicious input",
    }
    alice_headers = get_hmac_headers(log_path, "POST", alice_token)
    res_ingest = client.post(log_path, json=spoofed_payload, headers=alice_headers)
    assert res_ingest.status_code == 200

    # Verify Alice's log now contains this entry under Alice's uid, not Bob's
    alice_log_path = f"/api/v1/activity/log/{alice_uid}"
    alice_get_headers = get_hmac_headers(alice_log_path, "GET", alice_token)
    res_alice_self = client.get(alice_log_path, headers=alice_get_headers)
    assert res_alice_self.status_code == 200
    assert any(item.get("event") == "CHEAT_ATTEMPT" for item in res_alice_self.json()["items"])

    # Alice trying to access Bob's log should be 403 Forbidden
    bob_log_path = f"/api/v1/activity/log/{bob_uid}"
    alice_access_bob_headers = get_hmac_headers(bob_log_path, "GET", alice_token)
    res_alice_access_bob = client.get(bob_log_path, headers=alice_access_bob_headers)
    assert res_alice_access_bob.status_code == 403

    # Unauthenticated access to log should be 401
    res_anon = client.get(alice_log_path, headers=get_hmac_headers(alice_log_path, "GET"))
    assert res_anon.status_code == 401

    # Admin accessing Alice's log should be 200 OK
    admin_headers = get_hmac_headers(alice_log_path, "GET", admin_token)
    res_admin = client.get(alice_log_path, headers=admin_headers)
    assert res_admin.status_code == 200


def test_practice_and_ai_ownership_validation():
    alice_uid = f"alice_own_{uuid.uuid4().hex[:6]}"
    bob_uid = f"bob_own_{uuid.uuid4().hex[:6]}"
    alice_token = create_access_token({"sub": alice_uid, "name": "Alice", "role": "student"})
    bob_token = create_access_token({"sub": bob_uid, "name": "Bob", "role": "student"})

    # Alice submits a practice attempt
    attempt_id = f"prc_own_{uuid.uuid4().hex[:6]}"
    path_practice = "/api/v1/practice/submit"
    alice_practice_headers = get_hmac_headers(path_practice, "POST", alice_token)
    res1 = client.post(path_practice, json={
        "id": attempt_id,
        "type": "mcq",
        "question": "Q1",
        "is_correct": True,
    }, headers=alice_practice_headers)
    assert res1.status_code == 200

    # Bob attempts to overwrite Alice's practice attempt with the same ID -> 403
    bob_practice_headers = get_hmac_headers(path_practice, "POST", bob_token)
    res2 = client.post(path_practice, json={
        "id": attempt_id,
        "type": "mcq",
        "question": "Hacked Question",
        "is_correct": False,
    }, headers=bob_practice_headers)
    assert res2.status_code == 403

    # Alice saves an AI conversation telemetry
    conv_id = f"conv_own_{uuid.uuid4().hex[:6]}"
    path_ai = "/api/v1/ai/conversations/telemetry"
    alice_ai_headers = get_hmac_headers(path_ai, "POST", alice_token)
    res3 = client.post(path_ai, json={
        "conversationId": conv_id,
        "messages": [{"role": "user", "text": "Hello"}],
    }, headers=alice_ai_headers)
    assert res3.status_code == 200

    # Bob attempts to overwrite Alice's AI conversation telemetry -> 403
    bob_ai_headers = get_hmac_headers(path_ai, "POST", bob_token)
    res4 = client.post(path_ai, json={
        "conversationId": conv_id,
        "messages": [{"role": "user", "text": "Overwritten"}],
    }, headers=bob_ai_headers)
    assert res4.status_code == 403
