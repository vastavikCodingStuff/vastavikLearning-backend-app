from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.db.firebase import db

REVOKED_TOKEN_VERSIONS: Dict[str, int] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _query(coll_name: str, field: str, op: str, value: Any) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if db.use_live_firestore:
        from google.cloud.firestore_v1.base_query import FieldFilter
        coll = db.collection(coll_name)
        try:
            query = coll.where(filter=FieldFilter(field, op, value))
        except Exception:
            query = coll.where(field, op, value)
        for d in query.stream():
            data = d.to_dict() or {}
            data["id"] = d.id
            items.append(data)
        return items

    coll = db.collection(coll_name)
    for d in coll.stream():
        val = (d.to_dict() or {}).get(field)
        match = False
        if op == "==" and val == value:
            match = True
        elif op == "in" and isinstance(value, list) and val in value:
            match = True
        if match:
            data = d.to_dict() or {}
            data["id"] = d.id
            items.append(data)
    return items


def _save(coll_name: str, doc_id: str, data: Dict[str, Any]) -> None:
    coll = db.collection(coll_name)
    coll.document(doc_id).set(data, merge=True)


def _get_user(uid: str) -> Optional[Dict[str, Any]]:
    return db._memory_users.get(uid) if not db.use_live_firestore else None


async def log_device_event(uid: str, event: str, device_id: str, prev_device_id: Optional[str] = None,
                           platform: str = "", flagged: bool = False) -> Dict[str, Any]:
    entry = {
        "uid": uid,
        "event": event,
        "device_id": device_id,
        "prev_device_id": prev_device_id,
        "platform": platform,
        "flagged": flagged,
        "ts": _now(),
    }
    _save("device_events", f"{uid}_{int(datetime.now(timezone.utc).timestamp() * 1000)}", entry)
    return entry


def current_token_version(uid: str) -> int:
    return REVOKED_TOKEN_VERSIONS.get(uid, 0)


async def get_token_version(uid: str) -> int:
    user = await db.get_user_by_id(uid) or {}
    persisted = int(user.get("token_version", 0))
    cached = REVOKED_TOKEN_VERSIONS.get(uid, 0)
    tv = max(persisted, cached)
    REVOKED_TOKEN_VERSIONS[uid] = tv
    return tv


def is_token_version_valid(uid: str, token_tv: int) -> bool:
    return token_tv >= current_token_version(uid)


async def bind_device(uid: str, device_id: str, device_name: str = "", platform: str = "") -> Dict[str, Any]:
    """
    One device per account. If the user already has a different active device,
    the old one is force-logged-out: token_version is bumped and the old device
    loses access on its next API call (or within ACCESS_TOKEN_EXPIRE_MINUTES).
    Same-device-different-account activity is logged with flagged=True but is
    NOT enforced (pending product review).
    """
    user = await db.get_user_by_id(uid)
    if not user:
        await db.save_user({"uid": uid})

    active = user.get("active_device") or {}
    old_device_id = active.get("device_id")
    flagged = False

    if old_device_id and old_device_id != device_id:
        await log_device_event(uid, "duplicate_login", device_id, old_device_id, platform, flagged=False)
        await bump_token_version(uid)
        new_tv = current_token_version(uid)
    else:
        new_tv = current_token_version(uid)

    prev_uids = []
    if device_id:
        history = _query("device_bindings", "device_id", "==", device_id)
        for h in history:
            if h.get("uid") and h["uid"] != uid:
                prev_uids.append(h["uid"])
        if prev_uids:
            flagged = True
            await log_device_event(uid, "shared_device_suspect", device_id, None, platform, flagged=True)

    active_device = {
        "device_id": device_id,
        "device_name": device_name or "Unknown",
        "platform": platform or "unknown",
        "bound_at": active.get("bound_at") or _now(),
        "last_seen_at": _now(),
    }
    await db.update_user(uid, {
        "active_device": active_device,
        "token_version": new_tv,
    })
    REVOKED_TOKEN_VERSIONS[uid] = new_tv

    _save("device_bindings", device_id, {
        "device_id": device_id,
        "uid": uid,
        "device_name": active_device["device_name"],
        "platform": active_device["platform"],
        "last_seen_at": _now(),
    })

    await log_device_event(uid, "bind", device_id, old_device_id, platform, flagged=flagged)
    return {
        "active_device": active_device,
        "revoked_old_device": bool(old_device_id and old_device_id != device_id),
        "token_version": new_tv,
        "flagged": flagged,
    }


async def bump_token_version(uid: str) -> int:
    user = await db.get_user_by_id(uid) or {"uid": uid}
    new_tv = int(user.get("token_version", 0)) + 1
    await db.update_user(uid, {"token_version": new_tv})
    REVOKED_TOKEN_VERSIONS[uid] = new_tv
    return new_tv


async def unlink_device(uid: str) -> int:
    await db.update_user(uid, {
        "active_device": None,
    })
    return await bump_token_version(uid)
