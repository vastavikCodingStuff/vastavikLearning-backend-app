# Vastavik Learning Backend - Agent Guide

> This file is the canonical agent handbook for this repo. Keep it updated when architecture changes.

## Project Overview
- **Stack**: FastAPI + asyncio, Firebase Firestore (with in-memory fallback), JWT + HMAC-SHA256, Socket.IO, vanilla WebSockets, uvicorn
- **Deploy targets**: Self-hosted Linux VPS (2 vCPU/2-4GB), Railway, Render, Docker
- **Clients**: Web, Android, iOS, Desktop (all use same REST + Socket.IO contract)

## Key Directories
```
app/main.py                 # FastAPI entry, CORS, HMAC + security headers, circuit breaker, Socket.IO mount
app/core/config.py          # Pydantic Settings (.env)
app/core/security.py        # SHA-256+salt, JWT (15m/7d), HMAC verify
app/core/circuit_breaker.py # RouteStatusManager (ai_chat, code_execution, payments, conversations, socketio, peer_chat, ...)
app/core/rate_limiter.py    # Token-bucket (no Redis)
app/db/firebase.py          # Firestore + in-memory fallback (users, catalog, conversations/messages, notes, etc.)
app/models/schemas.py       # Pydantic schemas
app/routers/                # REST routers (auth, catalog, ai, code, realtime, conversations, doubts, payments, notes, pyq, search, system, admin)
app/realtime/socketio_server.py # Socket.IO AsyncServer (anytype conversations)
tests/test_backend.py       # 15+ integration tests
nginx/vastavik.conf         # NGINX (HTTP/2, WS/Socket.IO, rate limits)
systemd/ deploy.sh Dockerfile docker-compose.yml render.yaml railway.json
```

## Self-Hosted Real-Time Conversations (Socket.IO + Firestore)
### Data model (Firestore)
- `conversations/{conv_id}`: { id, type: direct|group|channel|support, title, avatar_url, created_by, participants: [uid], metadata, last_message, last_message_at, message_count, created_at, updated_at, is_archived }
- `conversations/{conv_id}/messages/{msg_id}`: { id, conversation_id, sender_id, sender_name, type: text|image|file|audio|video|system|code|location|custom, content, payload: {}, reply_to, reactions: {emoji:[uid]}, read_by: [uid], created_at, edited_at, deleted_at, temp_id }
- In-memory mirrors (`_memory_conversations`, `_memory_messages`) ensure zero-setup local dev and graceful fallback when composite index missing.

### REST API (`/api/v1/conversations` - requires JWT + HMAC, circuit breaker: `conversations`)
| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/conversations` | Create conversation (type, title, participant_ids) |
| GET | `/api/v1/conversations?limit&offset` | List my conversations (sorted updated_at desc, fallback without index) |
| GET | `/api/v1/conversations/{id}` | Get one conversation |
| PATCH | `/api/v1/conversations/{id}` | Update title/avatar/metadata |
| DELETE | `/api/v1/conversations/{id}` | Delete (creator/admin only) |
| POST | `/api/v1/conversations/{id}/participants` | Add participants |
| DELETE | `/api/v1/conversations/{id}/participants/{uid}` | Remove/leave |
| POST | `/api/v1/conversations/{id}/messages` | Send anytype message (REST fallback, also emits Socket.IO) |
| GET | `/api/v1/conversations/{id}/messages?limit&cursor` | History (cursor = msg id / created_at, newest-first) |
| PATCH | `/api/v1/conversations/{id}/messages/{msg_id}` | Edit (sender/admin) |
| DELETE | `/api/v1/conversations/{id}/messages/{msg_id}` | Soft-delete |
| POST | `/api/v1/conversations/{id}/messages/{msg_id}/read` | Mark read |
| POST | `/api/v1/conversations/{id}/messages/{msg_id}/react` | Add reaction {emoji} |
| DELETE | `/api/v1/conversations/{id}/messages/{msg_id}/react/{emoji}` | Remove reaction |

### Socket.IO (self-hosted, `python-socketio` AsyncServer @ `/socket.io/`)
- **Auth**: JWT access token via `auth: {token}` OR query `?token=` OR `Authorization: Bearer`. Verified with `decode_token`, type=access.
- **Server**: `app/realtime/socketio_server.py:sio` wrapped as `socketio.ASGIApp(sio, other_asgi_app=app)` exported as `app.main:socket_app`. Run `uvicorn app.main:socket_app` in prod; `app` still works for tests.
- **CORS**: `SOCKETIO_CORS_ORIGINS` (default `*`), polling+websocket, ping 25s/20s, 10MB buffer.
- **Bypass**: `/socket.io` and `/ws` bypass HMAC middleware.
- **Circuit breaker**: `socketio` + `conversations` features must be ON or connect is rejected; REST routes fast-fail 503 otherwise.
- **Rooms**: personal `user_{uid}` + conversation `conv_{conv_id}`. Auto-joins all user's conv rooms on connect.
- **Events**:
  - `connect` / `disconnect` (+ `connected`, `user_online/offline` broadcasts)
  - `join_conversation` {conversation_id} -> enters room, emits `user_joined`
  - `leave_conversation` {conversation_id}
  - `send_message` {conversation_id, type, content, payload, reply_to, temp_id} -> persists + emits `new_message` to room + `conversation_updated` to each participant's personal room
  - `typing` {conversation_id, is_typing} -> broadcast `typing`
  - `message_read` {conversation_id, message_id} -> `message_read`
  - `edit_message` / `delete_message` -> `message_edited` / `message_deleted`
  - `react` {conversation_id, message_id, emoji, action: add|remove|toggle} -> `reaction_updated`
  - `get_online_users` {conversation_id} -> {online_uids}
- **Anytype payload**: `payload` is free-form JSON. Examples:
  - image: `{file_url, mime, thumb_url, w, h}`
  - file: `{file_url, file_name, file_size, mime}`
  - code: `{language, code, stdin}`
  - location: `{lat, lng, address}`
  - custom: any JSON (poll, quiz, whiteboard state...)

### Config additions (.env / settings)
```
SOCKETIO_CORS_ORIGINS=*
SOCKETIO_ASYNC_MODE=asgi
MAX_CONVERSATION_PARTICIPANTS=100
MAX_MESSAGE_PAYLOAD_KB=64
CONVERSATION_HISTORY_PAGE_SIZE=50
CONVERSATION_HISTORY_MAX_LIMIT=100
RATE_LIMIT_CONVERSATIONS=60
RATE_LIMIT_SOCKET=300
```
### Client integration (all platforms)
- REST base: HMAC headers (`x-api-key-id/secret`, `x-timestamp`, `x-hmac`) + Bearer JWT
- Socket.IO client: `io("https://api.vastaviklearning.com", {auth:{token: accessToken}, transports:["websocket","polling"]})`
- For direct 1:1, create `direct` with 2 participants - deduped server-side
- Use `temp_id` for optimistic UI dedup (echoed back)
- History via REST, live via `new_message`; handle `conversation_updated` for badge counts
- Firestore composite index warning is auto-handled: server falls back to simple query + in-memory merge + python sort; create index via link in logs for prod scale.

## Auth, Security & Rate Limiting
- HMAC-SHA256 (5m replay window) enforced on all `/api/v1/*` except bypass list (`/health`, `/docs`, `/openapi.json`, `/socket.io`, `/ws`, `/uploads`, `/admin`)
- JWT Bearer for protected routes
- Token bucket in-memory (auth 5/m, ai 10/m, code 8/m, general 120/m, webrtc 600/m, conversations 60/m, socket connect 300/m)

## Testing
```bash
python -m pytest tests/test_backend.py -v
# + manual conversation check: see tests above or run snippet in .agents.md
```

## Graphify
- `graphify-out/` is gitignored. Regenerate with: `python -m graphify .` or `graphify .` (requires `graphifyy` package)
- After major feature (like conversations), rerun to update `graph.json`, `GRAPH_REPORT.md` (god nodes, surprising connections, suggested questions)
- Skill location: `~/.config/opencode/skills/graphify/`

## Deployment Notes
- `requirements.txt` includes `python-socketio>=5.10.0` (ASGI mode needs `uvicorn[standard]`)
- `app.main:socket_app` is the production ASGI entry (Socket.IO + FastAPI). `app.main:app` works for REST-only/tests
- NGINX `vastavik.conf` already proxies `/socket.io/` with `Upgrade` + 86400s timeouts
- Firestore: seed conversation index if you see `requires an index` warning; fallback handles it but index is recommended for >200 convs.

## Conventions
- Keep in-memory fallback in parity with Firestore writes (mirror + sort) for local dev
- Anytype messages: never assume payload schema - treat as `Dict[str, Any]`
- Use `file_path:line_number` when referencing code in PRs/reviews
- Prefer editing existing files over creating new ones; `write` only for new feature modules

<!-- sync: 2026-09-06 verified PR workflow -->
