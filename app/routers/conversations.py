"""
REST API for anytype conversations - CRUD + history.
Backs Socket.IO real-time layer with Firestore persistence.
Works for Web / Android / iOS / Desktop clients.
"""
import uuid
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status, Query

from app.core.security import get_current_user
from app.core.circuit_breaker import require_route_enabled
from app.core.config import settings
from app.db.firebase import db
from app.models.schemas import (
    CreateConversationRequest,
    AddParticipantsRequest,
    ConversationResponse,
    ConversationListResponse,
    SendMessageRequest,
    EditMessageRequest,
    ReactRequest,
    MessageResponse,
    MessageListResponse,
    CommonResponse,
)

router = APIRouter(prefix="/api/v1/conversations", tags=["Conversations & Messaging (Socket.IO)"])


def _conv_to_response(conv: Dict[str, Any]) -> ConversationResponse:
    return ConversationResponse(
        id=conv["id"],
        type=conv.get("type", "direct"),
        title=conv.get("title"),
        avatar_url=conv.get("avatar_url"),
        created_by=conv.get("created_by", ""),
        participants=conv.get("participants", []),
        participant_count=len(conv.get("participants", [])),
        last_message=conv.get("last_message"),
        last_message_at=conv.get("last_message_at"),
        message_count=conv.get("message_count", 0),
        created_at=conv.get("created_at", ""),
        updated_at=conv.get("updated_at", ""),
        is_archived=conv.get("is_archived", False),
        metadata=conv.get("metadata"),
    )


def _msg_to_response(msg: Dict[str, Any]) -> MessageResponse:
    return MessageResponse(
        id=msg["id"],
        conversation_id=msg.get("conversation_id", ""),
        sender_id=msg.get("sender_id", ""),
        sender_name=msg.get("sender_name"),
        sender_avatar=msg.get("sender_avatar"),
        type=msg.get("type", "text"),
        content=msg.get("content", ""),
        payload=msg.get("payload"),
        reply_to=msg.get("reply_to"),
        created_at=msg.get("created_at", ""),
        edited_at=msg.get("edited_at"),
        deleted_at=msg.get("deleted_at"),
        reactions=msg.get("reactions"),
        read_by=msg.get("read_by"),
        temp_id=msg.get("temp_id"),
    )


def _ensure_participant(conv: Dict[str, Any], uid: str, role: str = "student"):
    if uid not in conv.get("participants", []) and role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a participant of this conversation")


# -------------------------------------------------------
# Conversation CRUD
# -------------------------------------------------------
@router.post("", response_model=ConversationResponse, dependencies=[Depends(require_route_enabled("conversations"))])
async def create_conversation(
    request: CreateConversationRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    uid = current_user.get("sub")
    # Validate type
    allowed_types = {"direct", "group", "channel", "support"}
    conv_type = request.type.lower() if request.type else "direct"
    if conv_type not in allowed_types:
        raise HTTPException(status_code=400, detail=f"Invalid type. Allowed: {allowed_types}")

    # Build participant list (creator + requested)
    participants = list(set([uid] + (request.participant_ids or [])))
    if len(participants) > settings.MAX_CONVERSATION_PARTICIPANTS:
        raise HTTPException(status_code=400, detail=f"Too many participants (max {settings.MAX_CONVERSATION_PARTICIPANTS})")
    if conv_type == "direct" and len(participants) != 2:
        # For direct, we still allow creation but ideally 2. Don't enforce strictly for flexibility
        pass

    # Check for existing direct conversation dedup
    if conv_type == "direct" and len(participants) == 2:
        existing = await db.list_conversations_for_user(uid, limit=100)
        for conv in existing:
            if conv.get("type") == "direct" and set(conv.get("participants", [])) == set(participants):
                return _conv_to_response(conv)

    conv_data = {
        "type": conv_type,
        "title": request.title,
        "avatar_url": request.avatar_url,
        "created_by": uid,
        "participants": participants,
        "metadata": request.metadata,
        "message_count": 0,
        "is_archived": False,
    }
    saved = await db.create_conversation(conv_data)
    return _conv_to_response(saved)


@router.get("", response_model=ConversationListResponse, dependencies=[Depends(require_route_enabled("conversations"))])
async def list_conversations(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    uid = current_user.get("sub")
    convs = await db.list_conversations_for_user(uid, limit=limit, offset=offset)
    return ConversationListResponse(
        conversations=[_conv_to_response(c) for c in convs],
        total=len(convs),
    )


@router.get("/{conv_id}", response_model=ConversationResponse, dependencies=[Depends(require_route_enabled("conversations"))])
async def get_conversation(conv_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    conv = await db.get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    _ensure_participant(conv, current_user.get("sub"), current_user.get("role", "student"))
    return _conv_to_response(conv)


@router.patch("/{conv_id}", response_model=ConversationResponse, dependencies=[Depends(require_route_enabled("conversations"))])
async def update_conversation(
    conv_id: str,
    updates: Dict[str, Any],
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    conv = await db.get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    _ensure_participant(conv, current_user.get("sub"), current_user.get("role", "student"))
    # Only allow certain fields
    allowed = {"title", "avatar_url", "metadata", "is_archived"}
    filtered = {k: v for k, v in updates.items() if k in allowed}
    if not filtered:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    updated = await db.update_conversation(conv_id, filtered)
    return _conv_to_response(updated)


@router.delete("/{conv_id}", response_model=CommonResponse, dependencies=[Depends(require_route_enabled("conversations"))])
async def delete_conversation(conv_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    conv = await db.get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    # Only creator or admin can delete
    if conv.get("created_by") != current_user.get("sub") and current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only creator or admin can delete conversation")
    await db.delete_conversation(conv_id)
    return CommonResponse(success=True, message="Conversation deleted")


@router.post("/{conv_id}/participants", response_model=ConversationResponse, dependencies=[Depends(require_route_enabled("conversations"))])
async def add_participants(
    conv_id: str,
    request: AddParticipantsRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    conv = await db.get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    _ensure_participant(conv, current_user.get("sub"), current_user.get("role", "student"))
    if len(conv.get("participants", [])) + len(request.participant_ids) > settings.MAX_CONVERSATION_PARTICIPANTS:
        raise HTTPException(status_code=400, detail="Participant limit exceeded")
    updated = await db.add_participants(conv_id, request.participant_ids)
    # Emit system message? optional
    return _conv_to_response(updated)


@router.delete("/{conv_id}/participants/{user_id}", response_model=ConversationResponse, dependencies=[Depends(require_route_enabled("conversations"))])
async def remove_participant(
    conv_id: str,
    user_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    conv = await db.get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    # Allow self-leave or creator/admin
    if user_id != current_user.get("sub") and conv.get("created_by") != current_user.get("sub") and current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    updated = await db.remove_participant(conv_id, user_id)
    return _conv_to_response(updated)


# -------------------------------------------------------
# Messages REST (fallback + history)
# -------------------------------------------------------
@router.post("/{conv_id}/messages", response_model=MessageResponse, dependencies=[Depends(require_route_enabled("conversations"))])
async def send_message_rest(
    conv_id: str,
    request: SendMessageRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    conv = await db.get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    _ensure_participant(conv, current_user.get("sub"), current_user.get("role", "student"))

    allowed_types = {"text", "image", "file", "audio", "video", "system", "code", "location", "custom"}
    msg_type = request.type.lower() if request.type else "text"
    if msg_type not in allowed_types:
        msg_type = "custom"

    # Also broadcast via Socket.IO if available
    msg_doc = {
        "sender_id": current_user.get("sub"),
        "sender_name": current_user.get("name", "User"),
        "type": msg_type,
        "content": (request.content or "")[:10000],
        "payload": request.payload,
        "reply_to": request.reply_to,
        "temp_id": request.temp_id,
        "reactions": {},
        "read_by": [current_user.get("sub")],
    }
    saved = await db.create_message(conv_id, msg_doc)

    # Try to emit via socketio (best effort)
    try:
        from app.realtime.socketio_server import sio
        await sio.emit("new_message", saved, room=f"conv_{conv_id}")
        for pid in conv.get("participants", []):
            if pid != current_user.get("sub"):
                await sio.emit("conversation_updated", {"conversation_id": conv_id, "last_message": saved}, room=f"user_{pid}")
    except Exception:
        pass

    return _msg_to_response(saved)


@router.get("/{conv_id}/messages", response_model=MessageListResponse, dependencies=[Depends(require_route_enabled("conversations"))])
async def list_messages(
    conv_id: str,
    limit: int = Query(50, ge=1, le=100),
    cursor: Optional[str] = Query(None, description="Message ID or created_at for pagination"),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    conv = await db.get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    _ensure_participant(conv, current_user.get("sub"), current_user.get("role", "student"))

    msgs = await db.get_messages(conv_id, limit=limit, cursor=cursor)
    # msgs are descending; return as is (newest first) for cursor pagination
    next_cursor = msgs[-1]["id"] if len(msgs) == limit else None
    return MessageListResponse(
        messages=[_msg_to_response(m) for m in msgs],
        total=len(msgs),
        has_more=len(msgs) == limit,
        next_cursor=next_cursor,
    )


@router.patch("/{conv_id}/messages/{msg_id}", response_model=MessageResponse, dependencies=[Depends(require_route_enabled("conversations"))])
async def edit_message_rest(
    conv_id: str,
    msg_id: str,
    request: EditMessageRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    conv = await db.get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    msg = await db.get_message(conv_id, msg_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    if msg.get("sender_id") != current_user.get("sub") and current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only sender can edit")

    updates: Dict[str, Any] = {}
    if request.content is not None:
        updates["content"] = request.content[:10000]
    if request.payload is not None:
        updates["payload"] = request.payload

    updated = await db.update_message(conv_id, msg_id, updates)
    if not updated:
        raise HTTPException(status_code=404, detail="Message not found")
    try:
        from app.realtime.socketio_server import sio
        await sio.emit("message_edited", updated, room=f"conv_{conv_id}")
    except Exception:
        pass
    return _msg_to_response(updated)


@router.delete("/{conv_id}/messages/{msg_id}", response_model=CommonResponse, dependencies=[Depends(require_route_enabled("conversations"))])
async def delete_message_rest(
    conv_id: str,
    msg_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    msg = await db.get_message(conv_id, msg_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    if msg.get("sender_id") != current_user.get("sub") and current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only sender can delete")
    await db.delete_message(conv_id, msg_id)
    try:
        from app.realtime.socketio_server import sio
        await sio.emit("message_deleted", {"conversation_id": conv_id, "message_id": msg_id}, room=f"conv_{conv_id}")
    except Exception:
        pass
    return CommonResponse(success=True, message="Message deleted")


@router.post("/{conv_id}/messages/{msg_id}/read", response_model=CommonResponse, dependencies=[Depends(require_route_enabled("conversations"))])
async def mark_message_read_rest(
    conv_id: str,
    msg_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    await db.mark_read(conv_id, msg_id, current_user.get("sub"))
    try:
        from app.realtime.socketio_server import sio
        await sio.emit("message_read", {"conversation_id": conv_id, "message_id": msg_id, "uid": current_user.get("sub")}, room=f"conv_{conv_id}")
    except Exception:
        pass
    return CommonResponse(success=True, message="Marked as read")


@router.post("/{conv_id}/messages/{msg_id}/react", response_model=MessageResponse, dependencies=[Depends(require_route_enabled("conversations"))])
async def react_message_rest(
    conv_id: str,
    msg_id: str,
    request: ReactRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    updated = await db.add_reaction(conv_id, msg_id, current_user.get("sub"), request.emoji)
    if not updated:
        raise HTTPException(status_code=404, detail="Message not found")
    try:
        from app.realtime.socketio_server import sio
        await sio.emit("reaction_updated", updated, room=f"conv_{conv_id}")
    except Exception:
        pass
    return _msg_to_response(updated)


@router.delete("/{conv_id}/messages/{msg_id}/react/{emoji}", response_model=MessageResponse, dependencies=[Depends(require_route_enabled("conversations"))])
async def remove_reaction_rest(
    conv_id: str,
    msg_id: str,
    emoji: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    updated = await db.remove_reaction(conv_id, msg_id, current_user.get("sub"), emoji)
    if not updated:
        raise HTTPException(status_code=404, detail="Message not found")
    try:
        from app.realtime.socketio_server import sio
        await sio.emit("reaction_updated", updated, room=f"conv_{conv_id}")
    except Exception:
        pass
    return _msg_to_response(updated)
