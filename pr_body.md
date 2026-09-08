### Summary
- New `POST /api/v1/activity/log` endpoint that accepts a JSON array (or single object) of activity log entries and persists them.
- Adds Firestore collection `activity_logs` and an in-memory fallback in `DatabaseRepository`.
- Adds `GET /api/v1/activity/log/{uid}` for admin-side retrieval (newest first, configurable limit).
- Both endpoints are gated by HMAC (consistent with the rest of the API) and rate-limited at the 'general' bucket.

### Files
- `app/routers/activity.py` (new)
- `app/db/firebase.py` (added `_memory_activity_logs`, `save_activity_log`, `list_activity_logs`)
- `app/main.py` (mounted `activity.router`)
