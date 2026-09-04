import asyncio
import json
import logging
from typing import Dict, Set
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.circuit_breaker import route_manager

logger = logging.getLogger("vastavik.realtime")
router = APIRouter(tags=["Real-Time & WebSockets"])


class ConnectionHub:
    """
    In-memory hub managing active WebSocket connections for:
    1. Student Peer Chat rooms
    2. Live Classroom WebRTC SDP & ICE candidate signaling
    """
    def __init__(self):
        # room_id -> set of active WebSockets
        self.rooms: Dict[str, Set[WebSocket]] = {}
        # websocket -> metadata
        self.socket_meta: Dict[WebSocket, Dict[str, str]] = {}

    async def connect(self, websocket: WebSocket, room_id: str, user_id: str, role: str = "student"):
        await websocket.accept()
        if room_id not in self.rooms:
            self.rooms[room_id] = set()
        self.rooms[room_id].add(websocket)
        self.socket_meta[websocket] = {"room_id": room_id, "user_id": user_id, "role": role}

    def disconnect(self, websocket: WebSocket):
        meta = self.socket_meta.pop(websocket, None)
        if meta:
            room_id = meta.get("room_id")
            if room_id and room_id in self.rooms:
                self.rooms[room_id].discard(websocket)
                if not self.rooms[room_id]:
                    del self.rooms[room_id]

    async def broadcast_to_room(self, room_id: str, message: dict, exclude: WebSocket = None):
        """Broadcasts payload to all clients in a specific room."""
        if room_id not in self.rooms:
            return
        payload = json.dumps(message)
        dead_sockets = []
        for ws in self.rooms[room_id]:
            if ws != exclude:
                try:
                    await ws.send_text(payload)
                except Exception:
                    dead_sockets.append(ws)
        for dead in dead_sockets:
            self.disconnect(dead)


hub = ConnectionHub()


# ==========================================
# 1. Peer Chat WebSocket (/socket.io/ & /ws/peer-chat)
# ==========================================

async def handle_peer_chat(websocket: WebSocket, room_id: str, user_id: str):
    if not await route_manager.is_enabled("peer_chat"):
        await websocket.close(code=1008, reason="Peer chat is offline for maintenance.")
        return

    await hub.connect(websocket, room_id, user_id)
    # Broadcast join event
    await hub.broadcast_to_room(room_id, {
        "type": "USER_JOINED",
        "user_id": user_id,
        "room_id": room_id,
    }, exclude=websocket)

    try:
        while True:
            text = await websocket.receive_text()
            try:
                msg_data = json.loads(text)
            except Exception:
                msg_data = {"type": "MESSAGE", "text": text}

            msg_data["sender_id"] = user_id
            await hub.broadcast_to_room(room_id, msg_data, exclude=websocket)
    except WebSocketDisconnect:
        hub.disconnect(websocket)
        await hub.broadcast_to_room(room_id, {
            "type": "USER_LEFT",
            "user_id": user_id,
            "room_id": room_id,
        })


@router.websocket("/socket.io/")
async def socketio_compatibility_ws(websocket: WebSocket, room: str = "global", user_id: str = "anon"):
    """
    Socket.IO compatibility endpoint fallback for Android client's StudentConversationManager.
    """
    await handle_peer_chat(websocket, room, user_id)


@router.websocket("/ws/peer-chat")
async def peer_chat_ws(websocket: WebSocket, room: str = "global", user_id: str = "anon"):
    """
    Direct WebSocket connection for real-time peer discussion channels.
    """
    await handle_peer_chat(websocket, room, user_id)


# ==========================================
# 2. Live Classroom WebRTC Signaling Relay
# ==========================================

@router.websocket("/ws/signaling/{class_id}")
async def webrtc_signaling_ws(websocket: WebSocket, class_id: str, user_id: str = "student", role: str = "student"):
    """
    High-speed in-memory signaling bus for WebRTC:
    Exchanges SDP Offers, SDP Answers, and ICE candidates with sub-5ms latency.
    """
    if not await route_manager.is_enabled("live_signaling"):
        await websocket.close(code=1008, reason="Live classroom signaling is temporarily offline.")
        return

    await hub.connect(websocket, f"class_{class_id}", user_id, role)

    try:
        while True:
            text = await websocket.receive_text()
            data = json.loads(text)
            # data schema: { "target_id": "...", "signal_type": "OFFER|ANSWER|ICE|WHITEBOARD", "payload": ... }
            target_id = data.get("target_id")
            data["sender_id"] = user_id

            if target_id:
                # Targeted routing to specific peer
                room_sockets = hub.rooms.get(f"class_{class_id}", set())
                for peer_ws in room_sockets:
                    meta = hub.socket_meta.get(peer_ws, {})
                    if meta.get("user_id") == target_id:
                        try:
                            await peer_ws.send_text(json.dumps(data))
                        except Exception:
                            hub.disconnect(peer_ws)
                        break
            else:
                # Broadcast signal to all class participants (e.g. Whiteboard updates)
                await hub.broadcast_to_room(f"class_{class_id}", data, exclude=websocket)
    except WebSocketDisconnect:
        hub.disconnect(websocket)
        await hub.broadcast_to_room(f"class_{class_id}", {
            "signal_type": "PEER_DISCONNECTED",
            "user_id": user_id,
        })
