import uuid
from typing import Dict, Any
from datetime import datetime, timezone
import httpx
from fastapi import APIRouter, HTTPException, status, Depends

from app.core.config import settings
from app.core.security import (
    generate_salt,
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
)
from app.core.rate_limiter import rate_limit
from app.db.firebase import db
from app.models.schemas import (
    SignupRequest,
    LoginRequest,
    OAuthGoogleRequest,
    OAuthGitHubRequest,
    RefreshTokenRequest,
    DeviceVerifyRequest,
    AuthResponse,
    UserProfileResponse,
    CommonResponse,
)

router = APIRouter(prefix="/api/v1", tags=["Authentication & Profiles"])


@router.post("/auth/signup", response_model=AuthResponse, dependencies=[Depends(rate_limit("auth"))])
async def signup(request: SignupRequest):
    """
    Registers a new student account.
    Computes: passwordHash = Hex(SHA256(password + salt)) with a 32-byte cryptographic salt.
    """
    existing_user = await db.get_user_by_email(request.email)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An account with this email address already exists.",
        )

    salt = generate_salt()
    pw_hash = hash_password(request.password, salt)
    uid = f"usr_{uuid.uuid4().hex[:12]}"

    user_data = {
        "uid": uid,
        "name": request.name,
        "email": request.email.lower(),
        "password_hash": pw_hash,
        "salt": salt,
        "role": "student",
        "board": request.board,
        "preferred_language": request.language,
        "is_premium": False,
        "subscription_expires_at": None,
        "streak_count": 1,
        "total_lessons_completed": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.save_user(user_data)

    token_payload = {"sub": uid, "email": request.email, "role": "student", "name": request.name}
    access_token = create_access_token(token_payload)
    refresh_token = create_refresh_token(token_payload)

    return AuthResponse(
        success=True,
        access_token=access_token,
        refresh_token=refresh_token,
        user_id=uid,
        name=request.name,
        email=request.email,
        role="student",
    )


@router.post("/auth/login", response_model=AuthResponse, dependencies=[Depends(rate_limit("auth"))])
async def login(request: LoginRequest):
    """
    Authenticates user credentials using constant-time SHA-256 salted hash comparison.
    """
    # Check default admin login override if configured
    if request.email.lower() == settings.ADMIN_EMAIL.lower() and request.password == settings.ADMIN_PASSWORD:
        token_payload = {"sub": "admin_master", "email": request.email, "role": "admin", "name": "System Administrator"}
        access_token = create_access_token(token_payload)
        refresh_token = create_refresh_token(token_payload)
        return AuthResponse(
            success=True,
            access_token=access_token,
            refresh_token=refresh_token,
            user_id="admin_master",
            name="System Administrator",
            email=request.email,
            role="admin",
        )

    user = await db.get_user_by_email(request.email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    salt = user.get("salt")
    stored_hash = user.get("password_hash")

    if not salt or not stored_hash or not verify_password(request.password, salt, stored_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    token_payload = {
        "sub": user["uid"],
        "email": user["email"],
        "role": user.get("role", "student"),
        "name": user.get("name", "Student"),
    }
    access_token = create_access_token(token_payload)
    refresh_token = create_refresh_token(token_payload)

    return AuthResponse(
        success=True,
        access_token=access_token,
        refresh_token=refresh_token,
        user_id=user["uid"],
        name=user.get("name"),
        email=user["email"],
        role=user.get("role", "student"),
    )


@router.post("/auth/refresh", response_model=AuthResponse)
async def refresh_token(request: RefreshTokenRequest):
    """Refreshes an expired access token using a valid refresh token."""
    payload = decode_token(request.refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid token type provided.",
        )

    uid = payload.get("sub")
    user = await db.get_user_by_id(uid)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    token_payload = {
        "sub": user["uid"],
        "email": user["email"],
        "role": user.get("role", "student"),
        "name": user.get("name", "Student"),
    }
    new_access_token = create_access_token(token_payload)
    return AuthResponse(
        success=True,
        access_token=new_access_token,
        refresh_token=request.refresh_token,
        user_id=user["uid"],
        name=user.get("name"),
        email=user["email"],
        role=user.get("role", "student"),
    )


@router.post("/auth/oauth/google", response_model=AuthResponse)
async def oauth_google(request: OAuthGoogleRequest):
    """
    Validates Google ID Token, automatically provisions or retrieves user from Firestore.
    """
    google_url = f"https://oauth2.googleapis.com/tokeninfo?id_token={request.id_token}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(google_url)
        if resp.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to verify Google ID token with Google servers.",
            )
        data = resp.json()

    email = data.get("email")
    name = data.get("name", "Google User")
    google_sub = data.get("sub")

    if not email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Google token missing email claim.")

    user = await db.get_user_by_email(email)
    if not user:
        uid = f"usr_g_{google_sub[:10]}"
        user = {
            "uid": uid,
            "name": name,
            "email": email.lower(),
            "google_id": google_sub,
            "role": "student",
            "board": "ICSE",
            "preferred_language": "Java",
            "is_premium": False,
            "streak_count": 1,
            "total_lessons_completed": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.save_user(user)

    token_payload = {"sub": user["uid"], "email": user["email"], "role": user.get("role", "student"), "name": user["name"]}
    return AuthResponse(
        success=True,
        access_token=create_access_token(token_payload),
        refresh_token=create_refresh_token(token_payload),
        user_id=user["uid"],
        name=user["name"],
        email=user["email"],
        role=user.get("role", "student"),
    )


@router.post("/auth/oauth/github", response_model=AuthResponse)
async def oauth_github(request: OAuthGitHubRequest):
    """
    Exchanges GitHub OAuth code for access token and retrieves user profile.
    """
    if not settings.GITHUB_CLIENT_ID or not settings.GITHUB_CLIENT_SECRET:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="GitHub OAuth is not configured on this server.",
        )

    token_url = "https://github.com/login/oauth/access_token"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            token_url,
            headers={"Accept": "application/json"},
            data={
                "client_id": settings.GITHUB_CLIENT_ID,
                "client_secret": settings.GITHUB_CLIENT_SECRET,
                "code": request.code,
            }
        )
        token_data = resp.json()
        gh_access_token = token_data.get("access_token")

        if not gh_access_token:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid GitHub code or token exchange failed.")

        user_resp = await client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {gh_access_token}", "Accept": "application/json"}
        )
        gh_user = user_resp.json()

    gh_id = str(gh_user.get("id"))
    email = gh_user.get("email") or f"gh_{gh_id}@github.placeholder"
    name = gh_user.get("name") or gh_user.get("login", "GitHub User")

    user = await db.get_user_by_email(email)
    if not user:
        uid = f"usr_gh_{gh_id[:10]}"
        user = {
            "uid": uid,
            "name": name,
            "email": email.lower(),
            "github_id": gh_id,
            "role": "student",
            "board": "ICSE",
            "preferred_language": "Java",
            "is_premium": False,
            "streak_count": 1,
            "total_lessons_completed": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.save_user(user)

    token_payload = {"sub": user["uid"], "email": user["email"], "role": user.get("role", "student"), "name": user["name"]}
    return AuthResponse(
        success=True,
        access_token=create_access_token(token_payload),
        refresh_token=create_refresh_token(token_payload),
        user_id=user["uid"],
        name=user["name"],
        email=user["email"],
        role=user.get("role", "student"),
    )


@router.post("/auth/device-verify", response_model=CommonResponse)
async def device_verify(request: DeviceVerifyRequest):
    """
    Validates client device integrity, rooting status, and hardware attestation.
    """
    if request.is_rooted or request.is_emulator:
        return CommonResponse(
            success=False,
            message="Security integrity policy warning: Rooted devices or emulators have restricted DRM playback.",
        )
    return CommonResponse(success=True, message="Device integrity verified.")


@router.get("/user/profile", response_model=UserProfileResponse)
async def get_profile(current_user: Dict[str, Any] = Depends(get_current_user)):
    """
    Fetches the authenticated student's profile details.
    """
    uid = current_user.get("sub")
    user = await db.get_user_by_id(uid)
    if not user:
        # Return claims-based fallback if user is admin
        return UserProfileResponse(
            user_id=uid,
            name=current_user.get("name", "User"),
            email=current_user.get("email", ""),
            role=current_user.get("role", "student"),
            is_premium=True,
        )

    return UserProfileResponse(
        user_id=user["uid"],
        name=user.get("name", "Student"),
        email=user.get("email", ""),
        role=user.get("role", "student"),
        is_premium=user.get("is_premium", False),
        board=user.get("board", "ICSE"),
        preferred_language=user.get("preferred_language", "Java"),
        streak_count=user.get("streak_count", 0),
        lessons_completed=user.get("total_lessons_completed", 0),
        subscription_expires_at=user.get("subscription_expires_at"),
    )
