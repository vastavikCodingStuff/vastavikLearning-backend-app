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

def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """Create a short-lived JWT access token (default 15 mins)."""
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "iat": now, "type": "access"})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """Create a long-lived JWT refresh token (default 7 days)."""
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "iat": now, "type": "refresh"})
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
    """Extract and verify user claims from Bearer token."""
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
    return payload


async def require_admin_user(current_user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """Ensure that the authenticated user possesses the admin role."""
    if current_user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrative privileges required",
        )
    return current_user


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

    # Static key verification
    if not api_key_id or not api_key_secret:
        return False
    if not (hmac.compare_digest(api_key_id, settings.API_KEY_ID) and
            hmac.compare_digest(api_key_secret, settings.API_KEY_SECRET)):
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
        secret=settings.API_KEY_SECRET,
        timestamp=timestamp_str,
        method=request.method,
        path=request.url.path,
    )
    return hmac.compare_digest(client_hmac, expected_hmac)
