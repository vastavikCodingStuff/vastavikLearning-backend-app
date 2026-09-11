import os
import time
import uuid
from typing import Optional, List, Dict, Any
import httpx
from fastapi import APIRouter, HTTPException, status, Depends, UploadFile, File, Form

from app.core.config import settings
from app.core.security import get_current_user, get_current_user_optional, require_admin_user
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
    current_user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    """
    Accepts multipart bug report with device diagnostics and screenshot/log attachments.
    Stores files securely on disk and logs ticket to the database.
    """
    uid = current_user.get("sub") if current_user else "anonymous"
    saved_urls = []

    if media:
        os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
        allowed_bug_extensions = {".jpg", ".jpeg", ".png", ".webp", ".pdf", ".txt", ".log"}
        for upload in media:
            ext = os.path.splitext(upload.filename)[1].lower() if upload.filename else ".log"
            if ext not in allowed_bug_extensions:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Unsupported attachment format '{ext}'. Allowed: JPEG, PNG, WEBP, PDF, TXT, LOG."
                )
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


@router.get("/health/firestore")
async def firestore_health():
    """
    Exposes Firestore connectivity so admin/app can diagnose
    'things not loading' issues. Shows live vs in-memory mode,
    project id, and collection counts. No auth required (status only).
    """
    import logging
    logger = logging.getLogger("vastavik.health")
    info: Dict[str, Any] = {
        "use_live_firestore": bool(getattr(db, "use_live_firestore", False)),
        "project_id": getattr(settings, "FIREBASE_PROJECT_ID", ""),
        "collections": {},
        "warning": None,
    }
    try:
        for coll in ("courses", "videos", "users"):
            try:
                docs = list(db.collection(coll).stream())
                info["collections"][coll] = len(docs)
            except Exception as e:
                info["collections"][coll] = f"error: {e}"
    except Exception as e:
        info["warning"] = str(e)
    if not info["use_live_firestore"]:
        info["warning"] = (
            "Backend is running in IN-MEMORY mode (no Firestore credentials). "
            "Admin uploads will NOT appear in the app's direct Firestore listeners "
            "and will be lost on restart. Set FIREBASE_CREDENTIALS_BASE64 on Render."
        )
        logger.warning("Firestore health: in-memory mode")
    return info


@router.post("/admin/uploads/whiteboard")
async def upload_whiteboard_image(
    file: UploadFile = File(...),
    admin_user: Dict[str, Any] = Depends(require_admin_user),
):
    """
    Stores a whiteboard screenshot uploaded from the admin video modal
    under uploads/whiteboards and returns a public /uploads URL that can
    be saved as whiteboard_image_url on a video. Admin-only.
    """
    ext = os.path.splitext(file.filename)[1].lower() if file.filename else ".png"
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported image format '{ext}'. Allowed: JPG, PNG, WEBP.",
        )
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Image too large (max 5 MB).")
    dest_dir = os.path.join(settings.UPLOAD_DIR, "whiteboards")
    os.makedirs(dest_dir, exist_ok=True)
    filename = f"wb_{uuid.uuid4().hex[:10]}{ext}"
    with open(os.path.join(dest_dir, filename), "wb") as f:
        f.write(content)
    return {"success": True, "url": f"/uploads/whiteboards/{filename}"}
