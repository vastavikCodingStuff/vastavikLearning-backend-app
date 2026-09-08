import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

import jwt
from fastapi import Request, HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.core.config import settings

# HTTP Bearer security scheme for token authentication
security_bearer = HTTPBearer(auto_error=False)


# ==========================================
# 1. SHA-256 + 32-Byte Salt Password Hashing
# ==========================================

def generate_salt() -> str:
    """Generate 32 cryptographically secure random bytes as a 64-char hex string."""
    return secrets.token_hex(32)


def hash_password(password: str, salt: str) -> str:
    """Calculate SHA-256(password + salt) returning a 64-char hex string."""
    data = (password + salt).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def verify_password(plain_password: str, salt: str, stored_hash: str) -> bool:
    """Verify password against stored SHA-256 hash using constant-time comparison."""
    computed_hash = hash_password(plain_password, salt)
    return hmac.compare_digest(computed_hash, stored_hash)


# ==========================================
# 2. JWT Access & Refresh Token Management
# ==========================================

def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None, token_version: int = 0) -> str:
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "iat": now, "type": "access", "tv": token_version})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None, token_version: int = 0) -> str:
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "iat": now, "type": "refresh", "tv": token_version})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> Dict[str, Any]:
    """Decode and validate a JWT token."""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_bearer),
) -> Dict[str, Any]:
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_token(credentials.credentials)
    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
            headers={"WWW-Authenticate": "Bearer"},
        )
    uid = payload.get("sub")
    tv = payload.get("tv", 0)
    if uid and uid != "admin_master":
        # Check if user is in banned_students
        try:
            from app.db.firebase import db
            banned_doc = db.collection("banned_students").document(uid).get()
            if banned_doc.exists:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="ACCOUNT_BANNED: Your account has been banned and deleted by the administrator.",
                    headers={"WWW-Authenticate": "Bearer"},
                )
        except HTTPException:
            raise
        except Exception:
            pass

        # The single global admin uid ("admin_master") is intentionally exempt from
        # the in-memory token_version check: revocations on this uid would otherwise
        # silently boot every active admin session across all admins on every device.
        # Admin sessions are managed by the standard JWT expiry + refresh flow.
        from app.services.device_service import REVOKED_TOKEN_VERSIONS
        current_tv = REVOKED_TOKEN_VERSIONS.get(uid, 0)
        if tv < current_tv:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session revoked. Please log in again.",
                headers={"WWW-Authenticate": "Bearer"},
            )
    return payload


async def require_admin_user(current_user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """Ensure that the authenticated user possesses the admin role."""
    if current_user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrative privileges required",
        )
    return current_user


async def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_bearer),
) -> Optional[Dict[str, Any]]:
    """Like get_current_user but returns None instead of raising 401 if no token present.
    Used for optional auth on endpoints that work for both authenticated and anonymous users."""
    if not credentials:
        return None
    try:
        payload = decode_token(credentials.credentials)
        if payload.get("type") != "access":
            return None
        return payload
    except Exception:
        return None


# ==========================================
# 3. Dual-Layer HMAC-SHA256 Verification
# ==========================================

def compute_hmac_signature(secret: str, timestamp: str, method: str, path: str) -> str:
    """Compute HMAC-SHA256 signature for request verification."""
    message = f"{timestamp}{method.upper()}{path}"
    return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_hmac_headers(request: Request) -> bool:
    """
    Verifies x-api-key-id, x-api-key-secret, x-timestamp, and x-hmac.
    Protects against replay attacks with a 5-minute (300s) time window.
    """
    if not settings.ENFORCE_HMAC:
        return True

    api_key_id = request.headers.get("x-api-key-id")
    api_key_secret = request.headers.get("x-api-key-secret")
    timestamp_str = request.headers.get("x-timestamp")
    client_hmac = request.headers.get("x-hmac")

    # Static key verification (supports production settings or mobile client standard key pair)
    if not api_key_id or not api_key_secret:
        return False

    valid_key_pairs = [
        (settings.API_KEY_ID, settings.API_KEY_SECRET),
        ("dev-key-android-vastavik-001", "dev-secret-android-32bytes-hex-0000"),
    ]

    matched_secret = None
    for kid, ksecret in valid_key_pairs:
        if hmac.compare_digest(api_key_id, kid) and hmac.compare_digest(api_key_secret, ksecret):
            matched_secret = ksecret
            break

    if not matched_secret:
        return False

    # Timestamp & HMAC signature verification
    if not timestamp_str or not client_hmac:
        return False

    try:
        client_timestamp = float(timestamp_str)
        server_timestamp = time.time()
        # Replay attack window check: 300 seconds
        if abs(server_timestamp - client_timestamp) > 300:
            return False
    except ValueError:
        return False

    expected_hmac = compute_hmac_signature(
        secret=matched_secret,
        timestamp=timestamp_str,
        method=request.method,
        path=request.url.path,
    )
    return hmac.compare_digest(client_hmac, expected_hmac)
