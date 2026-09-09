import os
import uuid
from typing import Optional, Dict, Any
from fastapi import APIRouter, HTTPException, status, Depends, UploadFile, File, Form

from app.core.config import settings
from app.core.security import get_current_user
from app.core.circuit_breaker import require_route_enabled
from app.db.firebase import db
from app.models.schemas import CommonResponse

router = APIRouter(prefix="/api/v1", tags=["Doubt Engine"])


@router.post(
    "/doubts/submit",
    dependencies=[Depends(require_route_enabled("doubts"))]
)
async def submit_doubt(
    title: str = Form(...),
    question: str = Form(...),
    subject: str = Form(...),
    file: Optional[UploadFile] = File(None),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Submits a student doubt ticket with optional screenshot/photo attachment.
    """
    uid = current_user.get("sub")
    attachment_url = None

    if file:
        os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
        file_ext = os.path.splitext(file.filename)[1].lower() if file.filename else ".jpg"
        allowed_extensions = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}
        if file_ext not in allowed_extensions:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported file format '{file_ext}'. Allowed attachments: JPEG, PNG, WEBP, PDF."
            )
        file_name = f"doubt_{uuid.uuid4().hex[:10]}{file_ext}"
        file_path = os.path.join(settings.UPLOAD_DIR, file_name)

        content = await file.read()
        if len(content) > (settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024):
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Uploaded file exceeds {settings.MAX_UPLOAD_SIZE_MB} MB limit.",
            )

        with open(file_path, "wb") as f:
            f.write(content)

        attachment_url = f"/uploads/{file_name}"

    doubt_record = {
        "uid": uid,
        "student_name": current_user.get("name", "Student"),
        "title": title,
        "question": question,
        "subject": subject,
        "attachment_url": attachment_url,
        "status": "QUEUED",
    }
    saved = await db.create_doubt(doubt_record)

    return {
        "success": True,
        "ticket_id": saved["id"],
        "message": "Doubt ticket submitted successfully. Our educator team will review it shortly.",
    }
