import time
from typing import Dict, Tuple
from fastapi import Request, HTTPException, status
from app.core.config import settings


class TokenBucketLimiter:
    """
    In-memory Token Bucket rate limiter with sliding window tokens.
    Requires zero Redis dependency to maintain a tiny memory footprint on 2GB/4GB VPS.
    """
    def __init__(self):
        # key -> (tokens: float, last_updated: float, max_capacity: int, refill_rate_per_sec: float)
        self._buckets: Dict[str, Tuple[float, float, int, float]] = {}
        self._last_cleanup = time.time()

    def _cleanup_stale(self, now: float):
        """Prune keys inactive for more than 10 minutes to bound memory usage."""
        if now - self._last_cleanup > 600:
            stale_threshold = now - 600
            keys_to_delete = [
                k for k, (_, last_up, _, _) in self._buckets.items()
                if last_up < stale_threshold
            ]
            for k in keys_to_delete:
                del self._buckets[k]
            self._last_cleanup = now

    def is_allowed(self, key: str, max_requests: int, window_seconds: int = 60) -> bool:
        now = time.time()
        self._cleanup_stale(now)

        refill_rate = max_requests / float(window_seconds)

        if key not in self._buckets:
            # First request: starts with max_requests - 1
            self._buckets[key] = (max_requests - 1.0, now, max_requests, refill_rate)
            return True

        tokens, last_updated, capacity, rate = self._buckets[key]

        # Calculate refilled tokens based on elapsed time
        elapsed = now - last_updated
        new_tokens = min(float(capacity), tokens + (elapsed * rate))

        if new_tokens >= 1.0:
            self._buckets[key] = (new_tokens - 1.0, now, capacity, rate)
            return True
        else:
            self._buckets[key] = (new_tokens, now, capacity, rate)
            return False


limiter = TokenBucketLimiter()


def get_client_identifier(request: Request) -> str:
    """Extract real client IP considering NGINX / Cloudflare headers."""
    cf_connecting_ip = request.headers.get("cf-connecting-ip")
    if cf_connecting_ip:
        return cf_connecting_ip
    x_forwarded_for = request.headers.get("x-forwarded-for")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    x_real_ip = request.headers.get("x-real-ip")
    if x_real_ip:
        return x_real_ip
    return request.client.host if request.client else "127.0.0.1"


def rate_limit(limit_type: str = "general"):
    """
    FastAPI dependency for endpoint rate limiting.
    Supports limit types: 'auth', 'ai', 'code', 'general', 'webrtc'.
    """
    async def dependency(request: Request):
        client_ip = get_client_identifier(request)

        # Allow user-specific rate limiting if user is logged in
        auth_header = request.headers.get("authorization")
        user_id = auth_header.split(" ")[-1][:16] if auth_header and "Bearer" in auth_header else client_ip

        if limit_type == "auth":
            max_req = settings.RATE_LIMIT_AUTH
            key = f"auth:{client_ip}"
            err_msg = "Too many authentication attempts. Please try again in 1 minute."
        elif limit_type == "ai":
            max_req = settings.RATE_LIMIT_AI
            key = f"ai:{user_id}"
            err_msg = "AI quota cooling down. Please wait a moment before sending more queries."
        elif limit_type == "code":
            max_req = settings.RATE_LIMIT_CODE
            key = f"code:{user_id}"
            err_msg = "Execution queue is full for this minute. Please wait before re-submitting code."
        elif limit_type == "webrtc":
            max_req = settings.RATE_LIMIT_WEBRTC
            key = f"webrtc:{user_id}"
            err_msg = "Signaling rate limit exceeded."
        else:
            max_req = settings.RATE_LIMIT_GENERAL
            key = f"gen:{client_ip}"
            err_msg = "Too many requests. Please slow down."

        if not limiter.is_allowed(key, max_requests=max_req, window_seconds=60):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=err_msg,
                headers={"Retry-After": "60"},
            )
        return True

    return dependency
