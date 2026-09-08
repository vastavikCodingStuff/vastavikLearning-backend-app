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
