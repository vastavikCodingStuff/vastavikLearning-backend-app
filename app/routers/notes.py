from typing import List, Dict, Any
from fastapi import APIRouter, HTTPException, status, Depends

from app.core.security import get_current_user
from app.db.firebase import db
from app.models.schemas import NoteCreateRequest, NoteResponse, CommonResponse

router = APIRouter(prefix="/api/v1", tags=["Student Notes"])


@router.get("/notes", response_model=List[NoteResponse])
async def list_notes(current_user: Dict[str, Any] = Depends(get_current_user)):
    """
    Retrieves all notes saved by the authenticated student.
    """
    uid = current_user.get("sub")
    notes = await db.get_notes(uid)
    return notes


@router.post("/notes", response_model=NoteResponse)
async def create_note(request: NoteCreateRequest, current_user: Dict[str, Any] = Depends(get_current_user)):
    """
    Creates a new revision note for the student.
    """
    uid = current_user.get("sub")
    note_data = {
        "title": request.title,
        "content": request.content,
        "tag": request.tag or "General",
    }
    saved_note = await db.create_note(uid, note_data)
    return saved_note


@router.delete("/notes/{note_id}", response_model=CommonResponse)
async def delete_note(note_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """
    Deletes an existing note.
    """
    uid = current_user.get("sub")
    success = await db.delete_note(uid, note_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found or already removed.")
    return CommonResponse(success=True, message="Note deleted successfully.")
