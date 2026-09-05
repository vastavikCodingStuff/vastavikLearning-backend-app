"""
Socket.IO server for Vastavik Learning - anytype conversations.
Self-hosted, Firestore-persisted, cross-platform (Web, Android, iOS, Desktop).

Features:
- JWT authentication on connect (auth.token or query token)
- Rooms per conversation (conv_<id>)
- Events: join_conversation, leave_conversation, send_message, typing, message_read, edit_message, delete_message, reaction
- Persistence via DatabaseRepository (Firestore + in-memory fallback)
- Circuit breaker guard + rate limiting via token bucket
"""
import logging
import time
from typing import Dict, Any, Optional

import socketio

from app.core.config import settings
from app.core.security import decode_token
from app.core.circuit_breaker import route_manager
from app.core.rate_limiter import limiter
from app.db.firebase import db

logger = logging.getLogger("vastavik.socketio")

# -------------------------------------------------------
# Socket.IO Async Server - allow CORS *, transports polling+websocket
# -------------------------------------------------------
sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins=settings.get_socketio_cors_origins() if hasattr(settings, "get_socketio_cors_origins") else ["*"],
    logger=False,
    engineio_logger=False,
    ping_interval=25,
    ping_timeout=20,
    max_http_buffer_size=10 * 1024 * 1024,  # 10MB for file payloads
)

# sid -> user info {uid, email, name, role}
_connected_users: Dict[str, Dict[str, Any]] = {}
# uid -> set of sids (multi-device)
_user_sids: Dict[str, set] = {}


def _extract_token(auth: Optional[Dict[str, Any]], environ: Dict[str, Any]) -> Optional[str]:
    """Extract JWT from auth dict, query string, or headers."""
    if auth and isinstance(auth, dict):
        tok = auth.get("token") or auth.get("access_token")
        if tok:
            return tok
    # query string ?token=xxx
    qs = environ.get("QUERY_STRING", "")
    if qs:
        # QUERY_STRING may be bytes
        if isinstance(qs, bytes):
            qs = qs.decode("utf-8")
        for part in qs.split("&"):
            if part.startswith("token="):
                return part.split("=", 1)[1]
            if part.startswith("access_token="):
                return part.split("=", 1)[1]
    # headers
    headers = environ.get("HTTP_AUTHORIZATION", "")
    if headers and headers.startswith("Bearer "):
        return headers.split(" ", 1)[1]
    # fallback scope headers via environ
    return None


@sio.event
async def connect(sid, environ, auth):
    # Circuit breaker check
    if not await route_manager.is_enabled("socketio"):
        logger.warning(f"Socket.IO connect rejected (socketio offline) sid={sid}")
        return False  # reject
    if not await route_manager.is_enabled("conversations"):
        logger.warning(f"Socket.IO connect rejected (conversations offline) sid={sid}")
        return False

    token = _extract_token(auth, environ)
    if not token:
        logger.warning(f"Socket.IO connect rejected (no token) sid={sid}")
        return False

    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            logger.warning(f"Socket.IO invalid token type sid={sid}")
            return False
        uid = payload.get("sub")
        if not uid:
            return False
    except Exception as e:
        logger.warning(f"Socket.IO auth failed sid={sid} err={e}")
        return False

    # Rate limit connect per IP
    ip = environ.get("REMOTE_ADDR", "unknown")
    if not limiter.is_allowed(f"socket:connect:{ip}", max_requests=settings.RATE_LIMIT_SOCKET if hasattr(settings, "RATE_LIMIT_SOCKET") else 300, window_seconds=60):
        logger.warning(f"Socket.IO rate limited sid={sid} ip={ip}")
        return False

    user_info = {
        "uid": uid,
        "email": payload.get("email"),
        "name": payload.get("name", "User"),
        "role": payload.get("role", "student"),
        "sid": sid,
        "connected_at": time.time(),
    }
    _connected_users[sid] = user_info
    if uid not in _user_sids:
        _user_sids[uid] = set()
    _user_sids[uid].add(sid)

    # Auto-join personal room for direct notifications
    await sio.enter_room(sid, f"user_{uid}")

    # Auto-join all existing conversation rooms for this user (so offline messages can be pushed)
    try:
        convs = await db.list_conversations_for_user(uid, limit=100)
        for conv in convs:
            await sio.enter_room(sid, f"conv_{conv['id']}")
    except Exception as e:
        logger.warning(f"Auto-join conversations failed uid={uid} err={e}")

    logger.info(f"Socket.IO connected sid={sid} uid={uid}")
    await sio.emit("connected", {"sid": sid, "uid": uid, "message": "Socket.IO connected."}, to=sid)
    # Broadcast presence
    await sio.emit("user_online", {"uid": uid}, skip_sid=sid)
    return True


@sio.event
async def disconnect(sid):
    user = _connected_users.pop(sid, None)
    if user:
        uid = user.get("uid")
        if uid and uid in _user_sids:
            _user_sids[uid].discard(sid)
            if not _user_sids[uid]:
                del _user_sids[uid]
                await sio.emit("user_offline", {"uid": uid})
        logger.info(f"Socket.IO disconnected sid={sid} uid={uid}")


# -------------------------------------------------------
# Conversation room management
# -------------------------------------------------------
@sio.event
async def join_conversation(sid, data):
    user = _connected_users.get(sid)
    if not user:
        return {"success": False, "error": "UNAUTHORIZED"}
    uid = user["uid"]
    conv_id = data.get("conversation_id") if isinstance(data, dict) else str(data)
    if not conv_id:
        return {"success": False, "error": "MISSING_CONVERSATION_ID"}

    conv = await db.get_conversation(conv_id)
    if not conv:
        return {"success": False, "error": "CONVERSATION_NOT_FOUND"}
    if uid not in conv.get("participants", []):
        # Also allow admin
        if user.get("role") != "admin":
            return {"success": False, "error": "NOT_A_PARTICIPANT"}

    await sio.enter_room(sid, f"conv_{conv_id}")
    # Notify others
    await sio.emit("user_joined", {"conversation_id": conv_id, "uid": uid}, room=f"conv_{conv_id}", skip_sid=sid)
    return {"success": True, "conversation_id": conv_id}


@sio.event
async def leave_conversation(sid, data):
    user = _connected_users.get(sid)
    if not user:
        return {"success": False, "error": "UNAUTHORIZED"}
    uid = user["uid"]
    conv_id = data.get("conversation_id") if isinstance(data, dict) else str(data)
    if not conv_id:
        return {"success": False, "error": "MISSING_CONVERSATION_ID"}
    await sio.leave_room(sid, f"conv_{conv_id}")
    await sio.emit("user_left", {"conversation_id": conv_id, "uid": uid}, room=f"conv_{conv_id}", skip_sid=sid)
    return {"success": True}


# -------------------------------------------------------
# Messaging - anytype
# -------------------------------------------------------
@sio.event
async def send_message(sid, data):
    user = _connected_users.get(sid)
    if not user:
        return {"success": False, "error": "UNAUTHORIZED"}
    uid = user["uid"]
    if not isinstance(data, dict):
        return {"success": False, "error": "INVALID_PAYLOAD"}

    conv_id = data.get("conversation_id")
    if not conv_id:
        return {"success": False, "error": "MISSING_CONVERSATION_ID"}

    conv = await db.get_conversation(conv_id)
    if not conv:
        return {"success": False, "error": "CONVERSATION_NOT_FOUND"}
    if uid not in conv.get("participants", []) and user.get("role") != "admin":
        return {"success": False, "error": "NOT_A_PARTICIPANT"}

    # Rate limit per user
    if not limiter.is_allowed(f"socket:msg:{uid}", max_requests=60, window_seconds=60):
        return {"success": False, "error": "RATE_LIMITED", "message": "Slow down, too many messages."}

    msg_type = data.get("type", "text")
    allowed_types = {"text", "image", "file", "audio", "video", "system", "code", "location", "custom"}
    if msg_type not in allowed_types:
        msg_type = "custom"

    content = data.get("content", "") or ""
    payload = data.get("payload")
    reply_to = data.get("reply_to")
    temp_id = data.get("temp_id")

    # Payload size guard
    if payload and len(str(payload)) > settings.MAX_MESSAGE_PAYLOAD_KB * 1024 if hasattr(settings, "MAX_MESSAGE_PAYLOAD_KB") else 64*1024:
        return {"success": False, "error": "PAYLOAD_TOO_LARGE"}

    # Build message doc
    msg_doc = {
        "sender_id": uid,
        "sender_name": user.get("name"),
        "type": msg_type,
        "content": content[:10000],
        "payload": payload,
        "reply_to": reply_to,
        "temp_id": temp_id,
        "reactions": {},
        "read_by": [uid],
    }

    saved = await db.create_message(conv_id, msg_doc)
    # Broadcast to room (including sender for ack)
    await sio.emit("new_message", saved, room=f"conv_{conv_id}")
    # Also emit to each participant personal room for notification badge (even if not in conv room)
    for participant_id in conv.get("participants", []):
        if participant_id != uid:
            await sio.emit("conversation_updated", {
                "conversation_id": conv_id,
                "last_message": saved,
                "unread_increment": 1
            }, room=f"user_{participant_id}")

    return {"success": True, "message": saved}


@sio.event
async def typing(sid, data):
    user = _connected_users.get(sid)
    if not user:
        return
    uid = user["uid"]
    if not isinstance(data, dict):
        return
    conv_id = data.get("conversation_id")
    is_typing = data.get("is_typing", True)
    if not conv_id:
        return
    await sio.emit("typing", {"conversation_id": conv_id, "uid": uid, "is_typing": bool(is_typing), "name": user.get("name")}, room=f"conv_{conv_id}", skip_sid=sid)


@sio.event
async def message_read(sid, data):
    user = _connected_users.get(sid)
    if not user:
        return {"success": False}
    uid = user["uid"]
    if not isinstance(data, dict):
        return {"success": False}
    conv_id = data.get("conversation_id")
    msg_id = data.get("message_id")
    if not conv_id or not msg_id:
        return {"success": False, "error": "MISSING_FIELDS"}
    await db.mark_read(conv_id, msg_id, uid)
    await sio.emit("message_read", {"conversation_id": conv_id, "message_id": msg_id, "uid": uid}, room=f"conv_{conv_id}", skip_sid=sid)
    return {"success": True}


@sio.event
async def edit_message(sid, data):
    user = _connected_users.get(sid)
    if not user:
        return {"success": False, "error": "UNAUTHORIZED"}
    uid = user["uid"]
    if not isinstance(data, dict):
        return {"success": False}
    conv_id = data.get("conversation_id")
    msg_id = data.get("message_id")
    if not conv_id or not msg_id:
        return {"success": False, "error": "MISSING_FIELDS"}
    msg = await db.get_message(conv_id, msg_id)
    if not msg:
        return {"success": False, "error": "MESSAGE_NOT_FOUND"}
    if msg.get("sender_id") != uid and user.get("role") != "admin":
        return {"success": False, "error": "FORBIDDEN"}
    updates = {}
    if "content" in data and data["content"] is not None:
        updates["content"] = str(data["content"])[:10000]
    if "payload" in data:
        updates["payload"] = data["payload"]
    updated = await db.update_message(conv_id, msg_id, updates)
    if updated:
        await sio.emit("message_edited", updated, room=f"conv_{conv_id}")
    return {"success": True, "message": updated}


@sio.event
async def delete_message(sid, data):
    user = _connected_users.get(sid)
    if not user:
        return {"success": False, "error": "UNAUTHORIZED"}
    uid = user["uid"]
    if not isinstance(data, dict):
        return {"success": False}
    conv_id = data.get("conversation_id")
    msg_id = data.get("message_id")
    if not conv_id or not msg_id:
        return {"success": False, "error": "MISSING_FIELDS"}
    msg = await db.get_message(conv_id, msg_id)
    if not msg:
        return {"success": False, "error": "MESSAGE_NOT_FOUND"}
    if msg.get("sender_id") != uid and user.get("role") != "admin":
        return {"success": False, "error": "FORBIDDEN"}
    await db.delete_message(conv_id, msg_id)
    await sio.emit("message_deleted", {"conversation_id": conv_id, "message_id": msg_id}, room=f"conv_{conv_id}")
    return {"success": True}


@sio.event
async def react(sid, data):
    user = _connected_users.get(sid)
    if not user:
        return {"success": False}
    uid = user["uid"]
    if not isinstance(data, dict):
        return {"success": False}
    conv_id = data.get("conversation_id")
    msg_id = data.get("message_id")
    emoji = data.get("emoji")
    action = data.get("action", "add")  # add|remove|toggle
    if not conv_id or not msg_id or not emoji:
        return {"success": False, "error": "MISSING_FIELDS"}

    msg = await db.get_message(conv_id, msg_id)
    if not msg:
        return {"success": False, "error": "MESSAGE_NOT_FOUND"}

    reactions = msg.get("reactions") or {}
    if action == "remove":
        updated = await db.remove_reaction(conv_id, msg_id, uid, emoji)
    elif action == "toggle":
        if emoji in reactions and uid in reactions.get(emoji, []):
            updated = await db.remove_reaction(conv_id, msg_id, uid, emoji)
        else:
            updated = await db.add_reaction(conv_id, msg_id, uid, emoji)
    else:
        updated = await db.add_reaction(conv_id, msg_id, uid, emoji)

    if updated:
        await sio.emit("reaction_updated", updated, room=f"conv_{conv_id}")
    return {"success": True, "message": updated}


@sio.event
async def get_online_users(sid, data):
    # Return presence snapshot for a conversation
    user = _connected_users.get(sid)
    if not user:
        return {"success": False}
    conv_id = None
    if isinstance(data, dict):
        conv_id = data.get("conversation_id")
    else:
        conv_id = str(data) if data else None
    if not conv_id:
        return {"online_uids": list(_user_sids.keys())}
    conv = await db.get_conversation(conv_id)
    if not conv:
        return {"success": False, "error": "CONVERSATION_NOT_FOUND"}
    participants = conv.get("participants", [])
    online = [uid for uid in participants if uid in _user_sids]
    return {"success": True, "online_uids": online, "participants": participants}


# Helper to get sio instance
def get_socketio_server():
    return sio
