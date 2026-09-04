import os
import time
import uuid
from typing import Optional, List, Dict, Any
import httpx
from fastapi import APIRouter, HTTPException, status, Depends, UploadFile, File, Form

from app.core.config import settings
from app.core.security import get_current_user
from app.core.rate_limiter import rate_limit
from app.db.firebase import db
from app.models.schemas import AppUpdateResponse, FcmTokenRequest, CommonResponse

router = APIRouter(prefix="/api/v1", tags=["System & Diagnostics"])

# Cached GitHub App Release Info (1 Hour TTL)
_update_cache: Optional[Dict[str, Any]] = None
_update_cache_timestamp: float = 0.0
UPDATE_CACHE_TTL_SECONDS = 3600.0  # 1 hour


@router.post("/system/bug-report")
async def report_bug(
    title: str = Form(...),
    description: str = Form(...),
    category: str = Form(default="General"),
    device_diagnostics: str = Form(default="{}"),
    media: Optional[List[UploadFile]] = File(None),
    current_user: Optional[Dict[str, Any]] = Depends(get_current_user),
):
    """
    Accepts multipart bug report with device diagnostics and screenshot/log attachments.
    Stores files securely on disk and logs ticket to the database.
    """
    uid = current_user.get("sub") if current_user else "anonymous"
    saved_urls = []

    if media:
        os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
        for upload in media:
            ext = os.path.splitext(upload.filename)[1] if upload.filename else ".dat"
            filename = f"bug_{uuid.uuid4().hex[:8]}{ext}"
            file_path = os.path.join(settings.UPLOAD_DIR, filename)
            content = await upload.read()
            with open(file_path, "wb") as f:
                f.write(content)
            saved_urls.append(f"/uploads/{filename}")

    report_record = {
        "uid": uid,
        "title": title,
        "description": description,
        "category": category,
        "device_diagnostics": device_diagnostics,
        "media_urls": saved_urls,
    }
    saved = await db.create_bug_report(report_record)

    return {
        "success": True,
        "ticket_id": saved["ticket_id"],
        "message": f"Bug report {saved['ticket_id']} received. Thank you for helping us improve Vastavik Learning.",
    }


@router.get("/system/app-update", response_model=AppUpdateResponse, dependencies=[Depends(rate_limit("general"))])
async def check_app_update():
    """
    Returns latest app release information.
    Caches GitHub Release API responses for 1 hour to prevent HTTP 403 rate-limit
    failures on shared university / school Wi-Fi networks.
    """
    global _update_cache, _update_cache_timestamp
    now = time.time()

    if _update_cache is not None and (now - _update_cache_timestamp) < UPDATE_CACHE_TTL_SECONDS:
        return _update_cache

    default_update = {
        "version_name": "1.4.0",
        "version_code": 14,
        "download_url": "https://github.com/vastaviklearning/vastavikLearning-app/releases/latest/download/app-release.apk",
        "changelog": "• Ultra-low latency Live Classroom streaming\n• Vastavik AI Code Tutor integration\n• Bug fixes and ICSE/CBSE syllabus enhancements",
        "is_mandatory": False,
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                "https://api.github.com/repos/vastaviklearning/vastavikLearning-app/releases/latest",
                headers={"User-Agent": "Vastavik-Backend-Updater"}
            )
            if resp.status_code == 200:
                data = resp.json()
                tag = data.get("tag_name", "v1.4.0").lstrip("v")
                body = data.get("body", default_update["changelog"])
                assets = data.get("assets", [])
                download_url = assets[0]["browser_download_url"] if assets else default_update["download_url"]

                _update_cache = {
                    "version_name": tag,
                    "version_code": 14,
                    "download_url": download_url,
                    "changelog": body,
                    "is_mandatory": False,
                }
                _update_cache_timestamp = now
                return _update_cache
    except Exception:
        pass

    # Fallback to default cache
    _update_cache = default_update
    _update_cache_timestamp = now
    return default_update


@router.get("/notifications", response_model=List[Dict[str, Any]])
async def get_notifications(current_user: Dict[str, Any] = Depends(get_current_user)):
    """
    Fetches the notification inbox for the student.
    """
    uid = current_user.get("sub")
    notifs = await db.get_notifications(uid)
    return notifs


@router.post("/notifications/token", response_model=CommonResponse)
async def register_fcm_token(request: FcmTokenRequest, current_user: Dict[str, Any] = Depends(get_current_user)):
    """
    Registers client Firebase Cloud Messaging (FCM) device push token.
    """
    uid = current_user.get("sub")
    await db.save_fcm_token(uid, request.fcm_token)
    return CommonResponse(success=True, message="Device push token registered.")
