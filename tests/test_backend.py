import time
import uuid
import pytest
from starlette.testclient import TestClient

from app.main import app
from app.core.config import settings
from app.core.security import (
    generate_salt,
    hash_password,
    verify_password,
    create_access_token,
    decode_token,
    compute_hmac_signature,
)
from app.core.circuit_breaker import route_manager
from app.core.rate_limiter import limiter

client = TestClient(app)


def get_hmac_headers(path: str, method: str = "GET") -> dict:
    """Helper to generate valid HMAC request headers."""
    ts = str(time.time())
    sig = compute_hmac_signature(settings.API_KEY_SECRET, ts, method, path)
    return {
        "x-api-key-id": settings.API_KEY_ID,
        "x-api-key-secret": settings.API_KEY_SECRET,
        "x-timestamp": ts,
        "x-hmac": sig,
    }


def test_password_hashing_sha256_salt():
    """Verify SHA-256 + 32-byte cryptographic salt hashing mechanism."""
    salt1 = generate_salt()
    salt2 = generate_salt()
    assert len(salt1) == 64  # 32 bytes in hex
    assert len(salt2) == 64
    assert salt1 != salt2

    password = "SuperStudentPassword123!"
    hash1 = hash_password(password, salt1)
    hash2 = hash_password(password, salt2)
    assert len(hash1) == 64
    assert hash1 != hash2  # Salt diversification

    assert verify_password(password, salt1, hash1) is True
    assert verify_password("WrongPassword", salt1, hash1) is False
    assert verify_password(password, salt2, hash1) is False


def test_jwt_generation_and_decoding():
    """Verify JWT token claims, typing, and decode functionality."""
    payload = {"sub": "usr_test_123", "role": "student", "email": "test@vastavik.com"}
    token = create_access_token(payload)
    decoded = decode_token(token)

    assert decoded["sub"] == "usr_test_123"
    assert decoded["role"] == "student"
    assert decoded["type"] == "access"
    assert "exp" in decoded


def test_hmac_signature_validation():
    """Test HMAC-SHA256 signature generation and rejection of expired/tampered requests."""
    # Health endpoint bypasses HMAC
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "healthy"

    # Protected endpoint without HMAC should fail with 401 when ENFORCE_HMAC=True
    res_no_hmac = client.get("/api/v1/catalog/home")
    assert res_no_hmac.status_code == 401
    assert res_no_hmac.json()["error"] == "INVALID_HMAC_SIGNATURE"

    # Protected endpoint with valid HMAC headers should succeed
    headers = get_hmac_headers("/api/v1/catalog/home", "GET")
    res_valid = client.get("/api/v1/catalog/home", headers=headers)
    assert res_valid.status_code == 200
    data = res_valid.json()
    assert "courses" in data
    assert "banners" in data

    # Tampered timestamp (replay attack > 300 seconds ago)
    old_ts = str(time.time() - 400)
    sig_old = compute_hmac_signature(settings.API_KEY_SECRET, old_ts, "GET", "/api/v1/catalog/home")
    replay_headers = {
        "x-api-key-id": settings.API_KEY_ID,
        "x-api-key-secret": settings.API_KEY_SECRET,
        "x-timestamp": old_ts,
        "x-hmac": sig_old,
    }
    res_replay = client.get("/api/v1/catalog/home", headers=replay_headers)
    assert res_replay.status_code == 401


def test_auth_signup_and_login_flow():
    """Test student signup with SHA-256 salted password and subsequent login."""
    test_email = f"student_{int(time.time())}@vastaviklearning.com"
    signup_payload = {
        "email": test_email,
        "password": "Password123!",
        "name": "Parth",
        "board": "ICSE",
        "language": "Java"
    }
    headers = get_hmac_headers("/api/v1/auth/signup", "POST")
    res_signup = client.post("/api/v1/auth/signup", json=signup_payload, headers=headers)
    assert res_signup.status_code == 200
    data = res_signup.json()
    assert data["success"] is True
    assert data["email"] == test_email
    assert data["access_token"] is not None
    assert data["refresh_token"] is not None
    token = data["access_token"]

    # Profile lookup with bearer token
    profile_headers = get_hmac_headers("/api/v1/user/profile", "GET")
    profile_headers["Authorization"] = f"Bearer {token}"
    res_prof = client.get("/api/v1/user/profile", headers=profile_headers)
    assert res_prof.status_code == 200
    prof = res_prof.json()
    assert prof["email"] == test_email
    assert prof["name"] == "Parth"
    assert prof["board"] == "ICSE"

    # Login with correct credentials
    login_payload = {"email": test_email, "password": "Password123!"}
    login_headers = get_hmac_headers("/api/v1/auth/login", "POST")
    res_login = client.post("/api/v1/auth/login", json=login_payload, headers=login_headers)
    assert res_login.status_code == 200
    assert res_login.json()["success"] is True

    # Login with wrong password
    bad_login = {"email": test_email, "password": "WrongPassword!"}
    res_bad = client.post("/api/v1/auth/login", json=bad_login, headers=login_headers)
    assert res_bad.status_code == 401


@pytest.mark.asyncio
async def test_dynamic_circuit_breaker():
    """
    Test dynamic route toggling: when a route is toggled offline,
    the server instantly fast-fails with HTTP 503 and Retry-After header.
    """
    # Initially enabled
    await route_manager.set_status("ai_chat", True)
    headers = get_hmac_headers("/api/v1/ai/chat", "POST")
    payload = {"prompt": "What is encapsulation?", "model": "mistral-god"}
    res_online = client.post("/api/v1/ai/chat", json=payload, headers=headers)
    assert res_online.status_code == 200

    # Toggle offline (simulating maintenance or external outage)
    await route_manager.set_status("ai_chat", False)
    res_offline = client.post("/api/v1/ai/chat", json=payload, headers=headers)
    assert res_offline.status_code == 503
    assert res_offline.headers.get("retry-after") == "300"
    data_off = res_offline.json()
    assert data_off["status"] == "MAINTENANCE"
    assert data_off["error"] == "ROUTE_TEMPORARILY_OFFLINE"

    # Re-enable online
    await route_manager.set_status("ai_chat", True)
    res_restored = client.post("/api/v1/ai/chat", json=payload, headers=headers)
    assert res_restored.status_code == 200


def test_token_bucket_rate_limiting():
    """Verify in-memory Token Bucket rate limiter blocks excessive requests."""
    key = "test_rate_limit_bucket"
    # Allow 3 requests
    assert limiter.is_allowed(key, max_requests=3, window_seconds=60) is True
    assert limiter.is_allowed(key, max_requests=3, window_seconds=60) is True
    assert limiter.is_allowed(key, max_requests=3, window_seconds=60) is True
    # 4th request must be denied
    assert limiter.is_allowed(key, max_requests=3, window_seconds=60) is False


def test_ocr_cleaner():
    """Verify OCR typographical glitch fixer."""
    headers = get_hmac_headers("/api/v1/code/clean-ocr", "POST")
    ocr_payload = {
        "raw_ocr_text": "public class Test {\n System.out.printin(“Hello World”);\n",
        "language": "java"
    }
    res = client.post("/api/v1/code/clean-ocr", json=ocr_payload, headers=headers)
    assert res.status_code == 200
    cleaned = res.json()["cleaned_code"]
    assert "System.out.println" in cleaned
    assert '"Hello World"' in cleaned


def test_past_year_questions():
    """Verify PYQ query filter endpoint."""
    headers = get_hmac_headers("/api/v1/pyqs", "GET")
    res = client.get("/api/v1/pyqs?board=ICSE&year=2023", headers=headers)
    assert res.status_code == 200
    pyqs = res.json()
    assert len(pyqs) >= 1
    assert pyqs[0]["board"] == "ICSE"
    assert str(pyqs[0]["year"]) == "2023"


def test_admin_routes_and_circuit_breaker_toggle():
    """Test admin authentication and dynamic route toggling endpoint."""
    # Login as default admin
    admin_login = {"email": settings.ADMIN_EMAIL, "password": settings.ADMIN_PASSWORD}
    admin_res = client.post("/api/v1/auth/login", json=admin_login, headers=get_hmac_headers("/api/v1/auth/login", "POST"))
    assert admin_res.status_code == 200
    admin_token = admin_res.json()["access_token"]

    auth_hdr = {"Authorization": f"Bearer {admin_token}"}

    # Fetch all route statuses
    res_routes = client.get("/admin/routes", headers=auth_hdr)
    assert res_routes.status_code == 200
    routes = res_routes.json()
    assert "ai_chat" in routes

    # Toggle 'code_execution' feature
    res_toggle = client.post("/admin/routes/code_execution/toggle", headers=auth_hdr)
    assert res_toggle.status_code == 200
    toggled_data = res_toggle.json()
    assert toggled_data["feature"] == "code_execution"

    # Reset back to True
    res_reset = client.post("/admin/routes/code_execution/set", json={"enabled": True}, headers=auth_hdr)
    assert res_reset.status_code == 200
    assert res_reset.json()["enabled"] is True


def test_notes_crud_lifecycle():
    """Test student notes creation, retrieval, and deletion."""
    # Create test user token
    token = create_access_token({"sub": "usr_notes_test", "role": "student", "email": "notes@vastavik.com"})
    headers = get_hmac_headers("/api/v1/notes", "POST")
    headers["Authorization"] = f"Bearer {token}"

    # Create note
    note_payload = {"title": "Binary Tree Traversal", "content": "Preorder: Root, Left, Right", "tag": "DSA"}
    res_create = client.post("/api/v1/notes", json=note_payload, headers=headers)
    assert res_create.status_code == 200
    note_data = res_create.json()
    note_id = note_data["id"]
    assert note_data["title"] == "Binary Tree Traversal"

    # List notes
    list_headers = get_hmac_headers("/api/v1/notes", "GET")
    list_headers["Authorization"] = f"Bearer {token}"
    res_list = client.get("/api/v1/notes", headers=list_headers)
    assert res_list.status_code == 200
    notes = res_list.json()
    assert any(n["id"] == note_id for n in notes)

    # Delete note
    del_path = f"/api/v1/notes/{note_id}"
    del_headers = get_hmac_headers(del_path, "DELETE")
    del_headers["Authorization"] = f"Bearer {token}"
    res_del = client.delete(del_path, headers=del_headers)
    assert res_del.status_code == 200
    assert res_del.json()["success"] is True


def test_payment_and_pro_subscription_unlock():
    """Test order creation and webhook processing unlocking Pro subscription."""
    token = create_access_token({"sub": "usr_pay_test", "role": "student", "email": "pay@vastavik.com"})
    headers = get_hmac_headers("/api/v1/payments/create-order", "POST")
    headers["Authorization"] = f"Bearer {token}"

    # Create order
    order_res = client.post("/api/v1/payments/create-order", json={"plan_id": "monthly_pro", "amount": 149.0}, headers=headers)
    assert order_res.status_code == 200
    order_data = order_res.json()
    order_id = order_data["order_id"]
    assert order_id.startswith("ORDER_")

    # Process webhook
    webhook_headers = get_hmac_headers("/api/v1/payments/webhook", "POST")
    webhook_payload = {
        "order_id": order_id,
        "transaction_id": "GATEWAY_TX_999",
        "status": "success",
        "signature": "mock_sig_for_test",
        "uid": "usr_pay_test",
        "amount": 149.0
    }
    wh_res = client.post("/api/v1/payments/webhook", json=webhook_payload, headers=webhook_headers)
    assert wh_res.status_code == 200
    assert wh_res.json()["success"] is True


def test_global_search_and_app_update():
    """Test full-text catalog search and app updater release caching."""
    # Search
    search_headers = get_hmac_headers("/api/v1/search", "GET")
    search_res = client.get("/api/v1/search?q=Java", headers=search_headers)
    assert search_res.status_code == 200
    results = search_res.json()
    assert "courses" in results
    assert len(results["courses"]) >= 1

    # App update
    up_headers = get_hmac_headers("/api/v1/system/app-update", "GET")
    up_res = client.get("/api/v1/system/app-update", headers=up_headers)
    assert up_res.status_code == 200
    up_data = up_res.json()
    assert "version_name" in up_data
    assert "download_url" in up_data


def test_websocket_peer_chat_and_signaling():
    """Test real-time WebSocket communication for peer chat and WebRTC signaling."""
    # Peer Chat WebSocket
    with client.websocket_connect("/ws/peer-chat?room=cs101&user_id=student_1") as ws1:
        with client.websocket_connect("/ws/peer-chat?room=cs101&user_id=student_2") as ws2:
            # ws1 (already in the room) receives notification that student_2 joined
            join_msg = ws1.receive_json()
            assert join_msg["type"] == "USER_JOINED"
            assert join_msg["user_id"] == "student_2"

            # student_1 sends message to the room
            ws1.send_json({"type": "MESSAGE", "text": "Hello classmates!"})
            msg = ws2.receive_json()
            assert msg["text"] == "Hello classmates!"
            assert msg["sender_id"] == "student_1"

    # WebRTC Classroom Signaling WebSocket
    with client.websocket_connect("/ws/signaling/room_101?user_id=teacher&role=teacher") as teacher_ws:
        with client.websocket_connect("/ws/signaling/room_101?user_id=student_a&role=student") as student_ws:
            # Teacher broadcasts whiteboard draw packet
            whiteboard_packet = {
                "signal_type": "WHITEBOARD",
                "payload_json": '{"action":"draw_line","x0":10,"y0":10,"x1":50,"y1":50}'
            }
            teacher_ws.send_json(whiteboard_packet)
            received = student_ws.receive_json()
            assert received["signal_type"] == "WHITEBOARD"
            assert received["sender_id"] == "teacher"


def test_judge0_fail_fast_resilience():
    """Verify that Judge0 proxy fails fast and handles offline status gracefully."""
    headers = get_hmac_headers("/api/v1/code/execute", "POST")
    payload = {
        "language": "python",
        "source_code": "print('Hello from test')",
        "stdin": ""
    }
    # External IP will either respond or fail fast with EXECUTION_ENGINE_OFFLINE
    res = client.post("/api/v1/code/execute", json=payload, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "success" in data
    assert "status_description" in data


def test_security_headers():
    """Verify production security headers are attached to responses."""
    res = client.get("/health")
    assert res.status_code == 200
    assert res.headers.get("x-content-type-options") == "nosniff"
    assert res.headers.get("x-frame-options") == "DENY"
    assert res.headers.get("x-xss-protection") == "1; mode=block"
    assert res.headers.get("referrer-policy") == "strict-origin-when-cross-origin"


def test_practice_sir_endpoints():
    """Verify student Practice Sir endpoints: MCQs, Coding, Predict Output, and Quiz sets."""
    # 1. MCQs
    h = get_hmac_headers("/api/v1/practice/mcq")
    res = client.get("/api/v1/practice/mcq?subject=Java&source=sir", headers=h)
    assert res.status_code == 200
    mcqs = res.json()
    assert len(mcqs) > 0
    assert mcqs[0]["source"] == "sir"
    assert "question" in mcqs[0]
    assert "options" in mcqs[0]
    assert "correct_index" in mcqs[0]

    # 2. Coding Exercises
    h = get_hmac_headers("/api/v1/practice/coding")
    res = client.get("/api/v1/practice/coding?language=java&source=sir", headers=h)
    assert res.status_code == 200
    coding = res.json()
    assert len(coding) > 0
    assert "starter_code" in coding[0]
    assert "test_cases" in coding[0]

    # 3. Predict Output
    h = get_hmac_headers("/api/v1/practice/predict-output")
    res = client.get("/api/v1/practice/predict-output?source=sir", headers=h)
    assert res.status_code == 200
    po = res.json()
    assert len(po) > 0
    assert "code_snippet" in po[0]

    # 4. Quizzes
    h = get_hmac_headers("/api/v1/practice/quiz")
    res = client.get("/api/v1/practice/quiz", headers=h)
    assert res.status_code == 200
    quizzes = res.json()
    assert len(quizzes) > 0


def test_video_lecture_formats():
    """Verify that lesson responses contain video_format for all 3 lecture types."""
    h = get_hmac_headers("/api/v1/lessons/lesson_oop_101")
    res = client.get("/api/v1/lessons/lesson_oop_101", headers=h)
    assert res.status_code == 200
    data = res.json()
    assert "video_format" in data
    assert data["video_format"] in ["screen_recording", "whiteboard", "short"]
    assert "youtube_url" in data
    assert "whiteboard_image_url" in data
    assert "code_sample" in data


def test_course_completion_and_progress_summary():
    """Verify course completion calculation and summary progress."""
    token = create_access_token({"sub": "student_test_123", "email": "test@vastavik.com", "role": "student"})
    auth_headers = {"Authorization": f"Bearer {token}"}

    # 1. Mark part visited
    h = get_hmac_headers("/api/v1/progress/visited", "POST") | auth_headers
    res = client.post("/api/v1/progress/visited", json={"course_id": "course_java_icse", "part_id": "part_1"}, headers=h)
    assert res.status_code == 200

    # 2. Get specific course progress
    h = get_hmac_headers("/api/v1/courses/course_java_icse/progress") | auth_headers
    res = client.get("/api/v1/courses/course_java_icse/progress", headers=h)
    assert res.status_code == 200
    prog = res.json()
    assert prog["course_id"] == "course_java_icse"
    assert prog["completed_parts"] >= 1
    assert prog["completion_percent"] > 0

    # 3. Get overall progress summary
    h = get_hmac_headers("/api/v1/progress/summary") | auth_headers
    res = client.get("/api/v1/progress/summary", headers=h)
    assert res.status_code == 200
    summary = res.json()
    assert "total_courses_enrolled" in summary
    assert "overall_completion_percent" in summary


def test_student_profile_enrichment_and_update():
    """Verify enriched student details (class, board, languages, payment details, completion rate)."""
    uid = f"student_prof_{uuid.uuid4().hex[:8]}"
    token = create_access_token({"sub": uid, "email": f"{uid}@vastavik.com", "role": "student"})
    auth_headers = {"Authorization": f"Bearer {token}"}

    # 1. Get profile
    h = get_hmac_headers("/api/v1/user/profile") | auth_headers
    res = client.get("/api/v1/user/profile", headers=h)
    assert res.status_code == 200
    prof = res.json()
    assert "student_class" in prof
    assert "board" in prof
    assert "languages" in prof
    assert "payment_details" in prof
    assert "completion_rate" in prof
    assert "Java" in prof["languages"]
    assert "SQL" in prof["languages"]

    # 2. Update profile
    h = get_hmac_headers("/api/v1/user/profile", "PUT") | auth_headers
    update_data = {
        "student_class": "Class 12",
        "board": "CBSE",
        "preferred_language": "Python",
        "languages": ["Python", "SQL", "JavaScript"]
    }
    res = client.put("/api/v1/user/profile", json=update_data, headers=h)
    assert res.status_code == 200
    updated = res.json()
    assert updated["student_class"] == "Class 12"
    assert updated["board"] == "CBSE"
    assert updated["preferred_language"] == "Python"

