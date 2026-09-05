import asyncio
from typing import Dict
from fastapi import Request, HTTPException, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response


class RouteStatusManager:
    """
    In-memory thread-safe circuit breaker status manager.
    Enables instant runtime route enabling/disabling without server restarts.
    """
    def __init__(self):
        self._lock = asyncio.Lock()
        self._routes: Dict[str, bool] = {
            "ai_chat": True,
            "code_execution": True,
            "payments": True,
            "live_signaling": True,
            "peer_chat": True,
            "conversations": True,
            "socketio": True,
            "notes": True,
            "pyq": True,
            "doubts": True,
            "system_reports": True,
        }

    async def is_enabled(self, feature: str) -> bool:
        async with self._lock:
            return self._routes.get(feature, True)

    def is_enabled_sync(self, feature: str) -> bool:
        return self._routes.get(feature, True)

    async def set_status(self, feature: str, enabled: bool) -> bool:
        async with self._lock:
            self._routes[feature] = enabled
            return self._routes[feature]

    async def toggle(self, feature: str) -> bool:
        async with self._lock:
            current = self._routes.get(feature, True)
            self._routes[feature] = not current
            return self._routes[feature]

    async def get_all(self) -> Dict[str, bool]:
        async with self._lock:
            return self._routes.copy()


# Singleton instance shared across the application
route_manager = RouteStatusManager()


def require_route_enabled(feature: str):
    """
    FastAPI dependency to guard specific routes with the circuit breaker.
    Returns HTTP 503 if the feature is flagged offline.
    """
    async def dependency():
        if not await route_manager.is_enabled(feature):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "status": "MAINTENANCE",
                    "error": "ROUTE_TEMPORARILY_OFFLINE",
                    "message": "This specific feature is undergoing scheduled maintenance. All other app services remain fully operational.",
                    "feature": feature,
                },
                headers={"Retry-After": "300"},
            )
        return True
    return dependency


class CircuitBreakerMiddleware(BaseHTTPMiddleware):
    """
    Global middleware mapping specific URL prefixes to circuit breaker feature keys.
    """
    ROUTE_FEATURE_MAP = {
        "/api/v1/ai": "ai_chat",
        "/api/v1/code": "code_execution",
        "/api/v1/payments": "payments",
        "/api/v1/doubts": "doubts",
        "/api/v1/conversations": "conversations",
    }

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        for prefix, feature in self.ROUTE_FEATURE_MAP.items():
            if path.startswith(prefix):
                if not await route_manager.is_enabled(feature):
                    return JSONResponse(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        headers={"Retry-After": "300"},
                        content={
                            "status": "MAINTENANCE",
                            "error": "ROUTE_TEMPORARILY_OFFLINE",
                            "message": "This specific feature is undergoing scheduled maintenance. All other app services remain fully operational.",
                            "feature": feature,
                        },
                    )
                break

        return await call_next(request)
