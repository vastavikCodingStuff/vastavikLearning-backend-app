import os
import time
from contextlib import asynccontextmanager
from typing import Dict, Any

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.security import verify_hmac_headers
from app.core.circuit_breaker import CircuitBreakerMiddleware, route_manager
from app.models.schemas import HealthResponse
from app.routers import (
    auth,
    catalog,
    ai,
    code,
    realtime,
    conversations,
    doubts,
    payments,
    notes,
    pyq,
    practice,
    search,
    system,
    admin,
    admin_dashboard,
    activity,
    growth,
)
from starlette.middleware.gzip import GZipMiddleware

SERVER_START_TIME = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Setup upload directory
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    yield
    # Teardown / cleanup if needed


app = FastAPI(
    title="Vastavik Learning Platform - Core Backend Engine",
    description="High-performance, memory-efficient backend powering Live Classrooms, AI Tutoring, Online Judge, and Course Delivery.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS Middleware configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Circuit Breaker Middleware (Fast-503 for disabled routes)
app.add_middleware(CircuitBreakerMiddleware)


# Security Headers Middleware
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """Inject industry standard HTTP security headers into all responses."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if settings.ENVIRONMENT == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# HMAC Verification Middleware
@app.middleware("http")
async def hmac_verification_middleware(request: Request, call_next):
    """
    Validates HMAC-SHA256 signature and 5-minute replay prevention headers
    on API requests when ENFORCE_HMAC is enabled in settings.
    """
    # CORS preflight never carries HMAC headers and must always pass through
    # so the browser can decide whether the actual cross-origin request is
    # allowed. Without this, every Vercel/Netlify/custom-domain caller would
    # see the OPTIONS 401 and the browser would block the real GET, causing
    # the admin web to fall through to its offline-cache code path.
    if request.method == "OPTIONS":
        return await call_next(request)

    path = request.url.path
    # Bypass endpoints that shouldn't require HMAC signatures
    bypass_paths = [
        "/docs",
        "/redoc",
        "/openapi.json",
        "/health",
        "/api/v1/health",
        "/api/v1/auth",
        "/auth",
        "/admin",
        "/api/v1/payments/razorpay/webhook",
        "/uploads",
        "/socket.io",
        "/ws",
    ]

    should_bypass = any(path.startswith(bp) for bp in bypass_paths)

    if not should_bypass and settings.ENFORCE_HMAC:
        if not verify_hmac_headers(request):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "status": "UNAUTHORIZED",
                    "error": "INVALID_HMAC_SIGNATURE",
                    "message": "Missing, expired, or invalid HMAC cryptographic request signature headers.",
                },
            )

    return await call_next(request)


# Static directory for uploaded homework, doubt screenshots, and bug logs
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")

# Mount API Routers
app.include_router(auth.router)
app.include_router(catalog.router)
app.include_router(ai.router)
app.include_router(code.router)
app.include_router(realtime.router)
app.include_router(conversations.router)
app.include_router(doubts.router)
app.include_router(payments.router)
app.include_router(notes.router)
app.include_router(pyq.router)
app.include_router(practice.router)
app.include_router(search.router)
app.include_router(system.router)
app.include_router(admin.router)
app.include_router(admin_dashboard.router)
app.include_router(admin.router, prefix="/api/v1")
app.include_router(admin_dashboard.router, prefix="/api/v1")
app.include_router(activity.router)
app.include_router(growth.router)
app.include_router(growth.admin_router)
app.include_router(growth.admin_router, prefix="/api/v1")

# Enable Gzip compression (minimum 1KB) for fast network transfer & low bandwidth usage
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Socket.IO ASGI integration - self-hosted real-time conversations
# Import here to avoid circular imports; mount only if python-socketio available
try:
    import socketio as _socketio  # noqa: F401
    from app.realtime.socketio_server import sio as _sio

    # Wrap FastAPI app with Socket.IO ASGI app for unified server
    # Mounted at /socket.io/ - compatible with Web, Android, iOS, Desktop clients
    # Using socketio.ASGIApp wrapper - create combined ASGI app
    socket_app = _socketio.ASGIApp(_sio, other_asgi_app=app)

    # Expose for uvicorn: when imported as app.main:socket_app it serves both
    # But keep `app` as primary for tests; socket_app is alternative entrypoint
except Exception as _e:
    # Fallback: Socket.IO not available, continue with REST only
    socket_app = app


@app.get("/health", response_model=HealthResponse, tags=["System & Diagnostics"])
@app.get("/api/v1/health", response_model=HealthResponse, tags=["System & Diagnostics"])
async def health_check():
    """
    Health check monitoring endpoint for Uptime monitors, NGINX upstreams, and Docker.
    """
    uptime = time.time() - SERVER_START_TIME
    statuses = await route_manager.get_all()
    return HealthResponse(
        status="healthy",
        uptime_seconds=uptime,
        route_status=statuses,
        environment=settings.ENVIRONMENT,
    )
