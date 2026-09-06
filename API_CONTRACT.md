# Vastavik Learning Platform — Full API Contract

> Canonical route reference for **Android (Kotlin)**, **Desktop (ElectronJS)**, and **Web (Next.js)** clients.
> Base URL (prod): `https://api.vastaviklearning.com`

---

## Authentication — All Requests

Every REST request to `/api/v1/*` (except bypassed paths) **must** include:

```
x-api-key-id: vastavik_prod_v1
x-api-key-secret: <SECRET>
x-timestamp: <UNIX_SECONDS>
x-hmac: <HMAC-SHA256(timestamp+METHOD+PATH, secret)>
Authorization: Bearer <JWT_ACCESS_TOKEN>   # for protected routes
```

**HMAC bypass (no headers needed):** `/health`, `/docs`, `/redoc`, `/openapi.json`, `/socket.io`, `/ws`, `/uploads`, `/admin`

**Rate limits (token bucket):**

| Scope | Limit | Bucket key |
|-------|-------|------------|
| Auth endpoints | 5 / min | per IP |
| AI chat | 10 / min | per user |
| Code execution | 8 / min | per user |
| Conversations | 60 / min | per user |
| General | 120 / min | per IP |
| WebRTC signaling | 600 / min | per user |

---

## 1. Auth & Profiles

### 1.1 `POST /api/v1/auth/signup`
Register a student account.

**Request:**
```json
{
  "email": "student@example.com",
  "password": "min6chars",
  "name": "Parth",
  "board": "ICSE",          // "ICSE" | "CBSE"
  "language": "Java"        // "Java" | "Python"
}
```

**Response `200`:**
```json
{
  "success": true,
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "user_id": "usr_abc123",
  "name": "Parth",
  "email": "student@example.com",
  "role": "student"
}
```

**Errors:** `400` duplicate email, `429` rate limit

---

### 1.2 `POST /api/v1/auth/login`
Authenticate with email/password. Admin override built-in.

**Request:**
```json
{
  "email": "student@example.com",
  "password": "min6chars",
  "device_fingerprint": "optional"
}
```

**Response `200`:** same shape as `1.1`

**Errors:** `401` bad credentials, `429` rate limit

---

### 1.3 `POST /api/v1/auth/refresh`
Refresh an expired access token.

**Request:**
```json
{
  "refresh_token": "eyJ..."
}
```

**Response `200`:** same shape as `1.1` (same refresh_token returned)

---

### 1.4 `POST /api/v1/auth/oauth/google`
Validate Google ID token, auto-provision user.

**Request:**
```json
{
  "id_token": "google_id_token_string"
}
```

**Response `200`:** same shape as `1.1`

---

### 1.5 `POST /api/v1/auth/oauth/github`
Exchange GitHub OAuth code for access.

**Request:**
```json
{
  "code": "github_oauth_code"
}
```

**Response `200`:** same shape as `1.1`

---

### 1.6 `POST /api/v1/auth/device-verify`
Validate device integrity (root/emulator detection).

**Request:**
```json
{
  "device_id": "android_id_or_udid",
  "is_rooted": false,
  "is_emulator": false,
  "integrity_token": "optional_attestation"
}
```

**Response `200`:**
```json
{
  "success": true,
  "message": "Device integrity verified."
}
```

---

### 1.7 `GET /api/v1/user/profile` *(JWT required)*
Fetch authenticated student profile.

**Response `200`:**
```json
{
  "user_id": "usr_abc123",
  "name": "Parth",
  "email": "student@example.com",
  "role": "student",
  "is_premium": false,
  "board": "ICSE",
  "preferred_language": "Java",
  "streak_count": 5,
  "lessons_completed": 12,
  "subscription_expires_at": "2026-12-01T00:00:00Z"
}
```

---

## 2. Courses & Curriculum

### 2.1 `GET /api/v1/catalog/home`
Home catalog bundle (courses, banners, topics). 5-min server cache.

**Response `200`:**
```json
{
  "courses": [
    {
      "id": "course_java_icse",
      "title": "Java for ICSE Class 10",
      "description": "Master OOPs, Arrays...",
      "icon_name": "java",
      "color": 13158659,
      "order": 1,
      "is_published": true
    }
  ],
  "banners": [
    {
      "id": "banner_1",
      "title": "Live Masterclass",
      "image_url": "https://...",
      "target_route": "live_lobby"
    }
  ],
  "popular_topics": [
    {
      "id": "topic_1",
      "name": "Object Oriented Programming",
      "tag": "Java"
    }
  ]
}
```

---

### 2.2 `GET /api/v1/courses/{course_id}/curriculum`
Course parts, subparts, and lesson IDs.

**Response `200`:**
```json
{
  "course_id": "course_java_icse",
  "parts": [
    {
      "part_id": "part_1",
      "title": "Introduction to OOPs",
      "order": 1,
      "subparts": [
        {
          "subpart_id": "sub_1",
          "title": "Classes, Objects and Encapsulation",
          "lesson_id": "lesson_oop_101"
        }
      ]
    }
  ]
}
```

---

### 2.3 `GET /api/v1/lessons/{lesson_id}`
Lesson details. Premium lessons require JWT + `is_premium` subscription.

**Response `200`:**
```json
{
  "id": "lesson_oop_101",
  "title": "Classes, Objects and Encapsulation",
  "description": "Detailed walk-through...",
  "youtube_url": "https://youtube.com/watch?v=...",
  "youtube_video_id": "dQw4w9WgXcQ",
  "duration_sec": 1420,
  "whiteboard_image_url": "https://...",
  "code_sample": "public class Student { ... }",
  "notes": "Remember: Encapsulation...",
  "is_premium": false,
  "order": 1
}
```

---

### 2.4 `POST /api/v1/progress/visited` *(JWT required)*
Mark curriculum part as completed.

**Request:**
```json
{
  "course_id": "course_java_icse",
  "part_id": "part_1"
}
```

**Response `200`:**
```json
{
  "success": true,
  "message": "Progress updated."
}
```

---

## 3. AI Tutoring

### 3.1 `POST /api/v1/ai/chat`
AI query with automatic fallback (Mistral → Gemini 3.7 → Gemini 3.6 → local).

**Request:**
```json
{
  "prompt": "What is encapsulation?",
  "model": "mistral-god",         // optional
  "history": [                     // optional
    {"role": "user", "content": "Hi"},
    {"role": "assistant", "content": "Hello!"}
  ]
}
```

**Response `200`:**
```json
{
  "reply": "Encapsulation is the bundling of data...",
  "model_used": "mistral-god",
  "is_fallback": false
}
```

**Circuit breaker:** `ai_chat` — returns `503` if offline.

---

### 3.2 `GET /api/v1/ai/chat/stream` *(SSE)*
Server-Sent Events word-by-word streaming.

**Query params:** `prompt` (required, min 1 char), `model` (optional)

**Response:** `text/event-stream`
```
data: Encapsulation
data:  is
data:  the
data:  bundling...
```

---

## 4. Code Execution

### 4.1 `POST /api/v1/code/execute`
Proxy to Judge0 online judge (10s fail-fast timeout).

**Request:**
```json
{
  "language": "java",          // "java" | "python" | "cpp" | "c" | "javascript"
  "source_code": "public class Main { ... }",
  "stdin": ""
}
```

**Response `200`:**
```json
{
  "success": true,
  "stdout": "Hello from test\n",
  "stderr": null,
  "execution_time": "0.12s",
  "memory_kb": 4096,
  "status_description": "Accepted"
}
```

**Circuit breaker:** `code_execution`

---

### 4.2 `POST /api/v1/code/clean-ocr`
Normalize OCR typographical errors in scanned code.

**Request:**
```json
{
  "raw_ocr_text": "public class Test {\n System.out.printin("Hello");\n",
  "language": "java"
}
```

**Response `200`:**
```json
{
  "cleaned_code": "public class Test {\n System.out.println(\"Hello\");\n}",
  "corrections_applied": ["printin → println"]
}
```

---

## 5. Conversations & Messaging

> **REST base** for CRUD + history. **Socket.IO** for real-time.
> Circuit breaker: `conversations` — all routes return `503` if offline.

### 5.1 `POST /api/v1/conversations` *(JWT required)*
Create a conversation. `direct` type auto-deduplicates (same 2 participants).

**Request:**
```json
{
  "type": "group",            // "direct" | "group" | "channel" | "support"
  "title": "Java Doubts",
  "participant_ids": ["usr_b", "usr_c"],
  "avatar_url": "https://...",   // optional
  "metadata": {}                  // optional free-form
}
```

**Response `200`:**
```json
{
  "id": "conv_abc123",
  "type": "group",
  "title": "Java Doubts",
  "avatar_url": null,
  "created_by": "usr_a",
  "participants": ["usr_a", "usr_b", "usr_c"],
  "participant_count": 3,
  "last_message": null,
  "last_message_at": null,
  "message_count": 0,
  "created_at": "2026-09-06T10:00:00Z",
  "updated_at": "2026-09-06T10:00:00Z",
  "is_archived": false,
  "metadata": null
}
```

---

### 5.2 `GET /api/v1/conversations` *(JWT required)*
List conversations for current user (sorted by `updated_at` desc).

**Query params:** `limit` (1–100, default 20), `offset` (default 0)

**Response `200`:**
```json
{
  "conversations": [ /* ConversationResponse[] */ ],
  "total": 5
}
```

---

### 5.3 `GET /api/v1/conversations/{conv_id}` *(JWT required)*

**Response `200`:** `ConversationResponse`

---

### 5.4 `PATCH /api/v1/conversations/{conv_id}` *(JWT required)*
Update allowed fields: `title`, `avatar_url`, `metadata`, `is_archived`.

**Request:**
```json
{
  "title": "New Title",
  "is_archived": true
}
```

**Response `200`:** `ConversationResponse`

---

### 5.5 `DELETE /api/v1/conversations/{conv_id}` *(JWT, creator/admin only)*

**Response `200`:**
```json
{
  "success": true,
  "message": "Conversation deleted"
}
```

---

### 5.6 `POST /api/v1/conversations/{conv_id}/participants` *(JWT required)*

**Request:**
```json
{
  "participant_ids": ["usr_d", "usr_e"]
}
```

**Response `200`:** `ConversationResponse`

---

### 5.7 `DELETE /api/v1/conversations/{conv_id}/participants/{user_id}` *(JWT required)*
Remove participant or self-leave.

**Response `200`:** `ConversationResponse`

---

### 5.8 `POST /api/v1/conversations/{conv_id}/messages` *(JWT required)*
Send anytype message (also emits Socket.IO `new_message`).

**Request:**
```json
{
  "type": "image",            // "text"|"image"|"file"|"audio"|"video"|"system"|"code"|"location"|"custom"
  "content": "Check this out",
  "payload": {
    "file_url": "https://storage.../img.jpg",
    "mime": "image/jpeg",
    "thumb_url": "https://storage.../thumb.jpg",
    "w": 1920,
    "h": 1080
  },
  "reply_to": "msg_xyz",       // optional
  "temp_id": "client_temp_001"  // optional, for optimistic UI dedup
}
```

**Response `200`:**
```json
{
  "id": "msg_abc123",
  "conversation_id": "conv_abc123",
  "sender_id": "usr_a",
  "sender_name": "Parth",
  "sender_avatar": null,
  "type": "image",
  "content": "Check this out",
  "payload": {
    "file_url": "https://storage.../img.jpg",
    "mime": "image/jpeg"
  },
  "reply_to": "msg_xyz",
  "created_at": "2026-09-06T10:05:00Z",
  "edited_at": null,
  "deleted_at": null,
  "reactions": {},
  "read_by": ["usr_a"],
  "temp_id": "client_temp_001"
}
```

**Anytype payload examples:**

| type | payload fields |
|------|---------------|
| `text` | `{}` or null |
| `image` | `{file_url, mime, thumb_url?, w?, h?}` |
| `file` | `{file_url, file_name, file_size, mime}` |
| `audio` | `{file_url, mime, duration_sec?}` |
| `video` | `{file_url, thumb_url, mime, duration_sec?, w?, h?}` |
| `code` | `{language, code, stdin?}` |
| `location` | `{lat, lng, address?}` |
| `custom` | any JSON (poll, quiz, whiteboard state) |

---

### 5.9 `GET /api/v1/conversations/{conv_id}/messages` *(JWT required)*
Message history (newest-first, cursor pagination).

**Query params:** `limit` (1–100, default 50), `cursor` (message ID or timestamp)

**Response `200`:**
```json
{
  "messages": [ /* MessageResponse[] */ ],
  "total": 50,
  "has_more": true,
  "next_cursor": "msg_last_id"
}
```

---

### 5.10 `PATCH /api/v1/conversations/{conv_id}/messages/{msg_id}` *(JWT, sender/admin only)*

**Request:**
```json
{
  "content": "Updated text",
  "payload": {}
}
```

**Response `200`:** `MessageResponse` (with `edited_at` set)

---

### 5.11 `DELETE /api/v1/conversations/{conv_id}/messages/{msg_id}` *(JWT, sender/admin only)*

**Response `200`:**
```json
{
  "success": true,
  "message": "Message deleted"
}
```

---

### 5.12 `POST /api/v1/conversations/{conv_id}/messages/{msg_id}/read` *(JWT required)*

**Response `200`:**
```json
{
  "success": true,
  "message": "Marked as read"
}
```

---

### 5.13 `POST /api/v1/conversations/{conv_id}/messages/{msg_id}/react` *(JWT required)*

**Request:**
```json
{
  "emoji": "👍"
}
```

**Response `200`:** `MessageResponse`

---

### 5.14 `DELETE /api/v1/conversations/{conv_id}/messages/{msg_id}/react/{emoji}` *(JWT required)*

**Response `200`:** `MessageResponse`

---

## 6. Doubt Engine

### 6.1 `POST /api/v1/doubts/submit` *(JWT required, multipart form)*

**Form fields:** `title`, `question`, `subject`, `file` (optional image)

**Response `200`:**
```json
{
  "success": true,
  "ticket_id": "DBT-ABC12345",
  "message": "Doubt submitted successfully."
}
```

---

## 7. Payments & Subscriptions

### 7.1 `POST /api/v1/payments/create-order` *(JWT required)*

**Request:**
```json
{
  "plan_id": "monthly_pro",
  "amount": 149.0
}
```

**Response `200`:**
```json
{
  "order_id": "ORDER_ABC123",
  "amount": 149.0,
  "currency": "INR",
  "checksum": "hmac_checksum",
  "payment_url": "https://gateway..."
}
```

---

### 7.2 `POST /api/v1/payments/webhook` *(HMAC only, no JWT)*

**Request:**
```json
{
  "order_id": "ORDER_ABC123",
  "transaction_id": "GATEWAY_TX_999",
  "status": "success",
  "signature": "gateway_signature",
  "uid": "usr_abc123",
  "amount": 149.0
}
```

**Response `200`:**
```json
{
  "success": true,
  "message": "Payment processed."
}
```

---

### 7.3 `GET /api/v1/payments/history` *(JWT required)*

**Response `200`:** `List[Dict]` — transaction history

---

## 8. Student Notes

### 8.1 `GET /api/v1/notes` *(JWT required)*

**Response `200`:**
```json
[
  {
    "id": "note_abc",
    "uid": "usr_abc123",
    "title": "Binary Tree Traversal",
    "content": "Preorder: Root, Left, Right",
    "tag": "DSA",
    "created_at": "2026-09-06T10:00:00Z"
  }
]
```

---

### 8.2 `POST /api/v1/notes` *(JWT required)*

**Request:**
```json
{
  "title": "Binary Tree Traversal",
  "content": "Preorder: Root, Left, Right",
  "tag": "DSA"
}
```

**Response `200`:** `NoteResponse`

---

### 8.3 `DELETE /api/v1/notes/{note_id}` *(JWT required)*

**Response `200`:**
```json
{
  "success": true,
  "message": "Note deleted."
}
```

---

## 9. Past Year Questions

### 9.1 `GET /api/v1/pyqs`

**Query params:** `board` (ICSE/CBSE), `year` (2023), `subject` (Computer)

**Response `200`:**
```json
[
  {
    "id": "pyq_icse_2023_1",
    "board": "ICSE",
    "year": "2023",
    "subject": "Computer Applications",
    "question": "Define autoboxing...",
    "solution": "Autoboxing is the automatic conversion...",
    "marks": 4
  }
]
```

---

## 10. Global Search

### 10.1 `GET /api/v1/search`

**Query params:** `q` (min 1 char)

**Response `200`:**
```json
{
  "query": "Java",
  "courses": [ /* CourseItem[] */ ],
  "topics": [ /* TopicItem[] */ ],
  "lessons": [ /* LessonResponse[] */ ]
}
```

---

## 11. System & Diagnostics

### 11.1 `POST /api/v1/system/bug-report` *(multipart form, JWT optional)*

**Form fields:** `title`, `description`, `category`, `device_diagnostics`, `media` (files)

**Response `200`:**
```json
{
  "success": true,
  "ticket_id": "VBUG-ABC123",
  "message": "Bug report received."
}
```

---

### 11.2 `GET /api/v1/system/app-update`

**Response `200`:**
```json
{
  "version_name": "2.1.0",
  "version_code": 42,
  "download_url": "https://github.com/.../releases/download/v2.1.0/app.apk",
  "changelog": "Bug fixes and performance improvements.",
  "is_mandatory": false
}
```

---

### 11.3 `GET /api/v1/notifications` *(JWT required)*

**Response `200`:**
```json
[
  {
    "id": "notif_welcome",
    "title": "Welcome to Vastavik Learning!",
    "message": "Start your journey...",
    "created_at": "2026-09-06T10:00:00Z",
    "is_read": false
  }
]
```

---

### 11.4 `POST /api/v1/notifications/token` *(JWT required)*

**Request:**
```json
{
  "fcm_token": "fcm_device_token_string"
}
```

**Response `200`:**
```json
{
  "success": true,
  "message": "FCM token registered."
}
```

---

## 12. Admin (Circuit Breaker Control)

> All `/admin` routes require `role: admin` JWT. HMAC-bypassed.

### 12.1 `GET /admin/routes`

**Response `200`:**
```json
{
  "ai_chat": true,
  "code_execution": true,
  "payments": true,
  "live_signaling": true,
  "peer_chat": true,
  "conversations": true,
  "socketio": true,
  "notes": true,
  "pyq": true,
  "doubts": true,
  "system_reports": true
}
```

---

### 12.2 `POST /admin/routes/{feature}/toggle`

**Response `200`:**
```json
{
  "success": true,
  "feature": "ai_chat",
  "enabled": false,
  "status_description": "MAINTENANCE (503 Fast-Fail Active)"
}
```

---

### 12.3 `POST /admin/routes/{feature}/set`

**Request:**
```json
{
  "enabled": true
}
```

**Response `200`:** same shape as `12.2`

---

## 13. Health

### 13.1 `GET /health` / `GET /api/v1/health` *(HMAC-bypassed)*

**Response `200`:**
```json
{
  "status": "healthy",
  "uptime_seconds": 3600.0,
  "route_status": { "ai_chat": true, ... },
  "environment": "production"
}
```

---

## 14. WebSockets (Legacy, Peer Chat & Signaling)

> These are vanilla WebSocket endpoints — **not** Socket.IO. Use the new Socket.IO for conversations.

### 14.1 `WS /ws/peer-chat?room={room}&user_id={uid}`

**Events (JSON):**
- `USER_JOINED` — `{"type":"USER_JOINED","user_id":"student_2","room_id":"cs101"}`
- `USER_LEFT` — `{"type":"USER_LEFT","user_id":"student_2","room_id":"cs101"}`
- `MESSAGE` — `{"type":"MESSAGE","text":"Hello","sender_id":"student_1"}`

---

### 14.2 `WS /ws/signaling/{class_id}?user_id={uid}&role={student|teacher}`

**Events (JSON):**
```json
{
  "target_id": "peer_uid",
  "signal_type": "OFFER|ANSWER|ICE|WHITEBOARD",
  "payload_json": "{\"sdp\":\"...\"}"
}
```

---

## 15. Socket.IO (Real-Time Conversations)

> **Endpoint:** `wss://api.vastaviklearning.com/socket.io/`
> **Transports:** `["websocket", "polling"]`
> **CORS:** `*`

### Connection

```
io("https://api.vastaviklearning.com", {
  auth: { token: "<JWT_ACCESS_TOKEN>" },
  transports: ["websocket", "polling"]
})
```

Auth also accepts `?token=<JWT>` query param or `Authorization: Bearer <JWT>` header.

**On connect:** server emits `connected`, enters personal room `user_{uid}`, auto-joins all `conv_{id}` rooms.

---

### Client → Server Events

| Event | Payload | Response |
|-------|---------|----------|
| `join_conversation` | `{conversation_id}` | `user_joined` broadcast to room |
| `leave_conversation` | `{conversation_id}` | `user_left` broadcast to room |
| `send_message` | `{conversation_id, type, content, payload?, reply_to?, temp_id?}` | `new_message` to room + `conversation_updated` to each participant's personal room |
| `typing` | `{conversation_id, is_typing}` | `typing` to room (skips sender) |
| `message_read` | `{conversation_id, message_id}` | `message_read` to room |
| `edit_message` | `{conversation_id, message_id, content?, payload?}` | `message_edited` to room |
| `delete_message` | `{conversation_id, message_id}` | `message_deleted` to room |
| `react` | `{conversation_id, message_id, emoji, action: "add"\|"remove"\|"toggle"}` | `reaction_updated` to room |
| `get_online_users` | `{conversation_id}` | returns `{online_uids, participants}` |

---

### Server → Client Events

| Event | Payload | When |
|-------|---------|------|
| `connected` | `{sid, uid, message}` | On successful connect |
| `user_online` | `{uid}` | Broadcast when any user connects |
| `user_offline` | `{uid}` | Broadcast when user's last device disconnects |
| `user_joined` | `{conversation_id, uid}` | User joined a conversation room |
| `user_left` | `{conversation_id, uid}` | User left a conversation room |
| `new_message` | `MessageResponse` | New message in a conversation you're in |
| `conversation_updated` | `{conversation_id, last_message, unread_increment}` | Badge update for personal room |
| `typing` | `{conversation_id, uid, is_typing, name}` | Typing indicator |
| `message_read` | `{conversation_id, message_id, uid}` | Read receipt |
| `message_edited` | `MessageResponse` | Message was edited |
| `message_deleted` | `{conversation_id, message_id}` | Message was soft-deleted |
| `reaction_updated` | `MessageResponse` | Reaction added/removed |

---

### Socket.IO Error Responses

All events return `{success: false, error: "ERROR_CODE"}` on failure:

| Error | Meaning |
|-------|---------|
| `UNAUTHORIZED` | No token or invalid JWT |
| `CONVERSATION_NOT_FOUND` | conv_id doesn't exist |
| `NOT_A_PARTICIPANT` | User not in participants list |
| `MISSING_CONVERSATION_ID` | conversation_id not provided |
| `PAYLOAD_TOO_LARGE` | payload exceeds 64KB |
| `RATE_LIMITED` | > 60 messages/min |
| `FORBIDDEN` | Not sender or admin |
| `MESSAGE_NOT_FOUND` | msg_id doesn't exist |
| `MISSING_FIELDS` | Required fields missing |

---

## Platform Integration Notes

### Android (Kotlin)
- Use `OkHttp` for REST with HMAC interceptor (`AuthInterceptor.kt` pattern)
- Socket.IO: `io.socket:socket.io-client:2.1.0` library
- FCM token: register via `POST /api/v1/notifications/token`

### Desktop (ElectronJS)
- Use `node-fetch` or `axios` for REST with HMAC signing
- Socket.IO: `socket.io-client` npm package
- JWT stored in `electron-store` or `keytar`

### Web (Next.js)
- Use `fetch` with HMAC middleware or `axios` interceptor
- Socket.IO: `socket.io-client` npm package
- JWT in `httpOnly` cookie or in-memory (not localStorage for prod)

---

*Last updated: 2026-09-06 | Commit: 18c9283d*
