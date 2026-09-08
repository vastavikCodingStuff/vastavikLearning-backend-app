# Student Ban & Separate Archival Architecture

## Overview
When an administrator deletes a student:
1. Complete snapshot of the student is preserved in `deleted_students_archive/{uid}`.
2. The user is added to `banned_students` (by UID and lowercase email).
3. Active JWT sessions and token versions are revoked via `revoke_user_sessions(uid)`.
4. Operational records in `users`, `studentSelections`, `refreshTokens`, notes, chats, and device bindings are wiped clean.
5. All future token authentications and login attempts are blocked with HTTP 403 `ACCOUNT_BANNED`.

## Admin Endpoints
- `DELETE /admin/students/{uid}`: Bans, archives snapshot, and cascades deletion.
- `GET /admin/students/archived`: Lists all archived students.
- `GET /admin/students/archived/{uid}`: Fetches complete snapshot for inspection.
- `GET /api/v1/auth/account-status`: Validates if client session is active, banned, or deleted.
