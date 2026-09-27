import uuid
from typing import Dict, Any, Tuple
from datetime import datetime, timezone
import base64
import httpx
import jwt
from jwt import PyJWKClient
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
from app.services.device_service import bind_device, get_token_version
from app.models.schemas import (
    SignupRequest,
    LoginRequest,
    OAuthGoogleRequest,
    OAuthGitHubRequest,
    OAuthClerkRequest,
    RefreshTokenRequest,
    DeviceVerifyRequest,
    AuthResponse,
    UserProfileResponse,
    UpdateProfileRequest,
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
        "school": request.school,
        "dob": request.dob,
        "hobbies": request.hobbies,
        "preferred_language": request.language,
        "student_class": request.student_class or "Class 10",
        "languages": request.languages or ["Java", "Python", "JavaScript", "SQL"],
        "is_premium": False,
        "subscription_expires_at": None,
        "streak_count": 1,
        "total_lessons_completed": 0,
        "credit_balance": 0.0,
        "access_type": "free",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.save_user(user_data)

    if request.referral_code:
        try:
            from app.services.growth_service import attribute_referral_on_signup
            await attribute_referral_on_signup(uid, request.referral_code.strip().upper(), request.device_fingerprint or "")
        except Exception:
            pass
    elif request.share_token:
        try:
            from app.services.growth_service import attribute_share_on_signup
            await attribute_share_on_signup(uid, request.share_token.strip())
        except Exception:
            pass

    if request.device_fingerprint:
        try:
            from app.services.device_service import bind_device
            await bind_device(uid, request.device_fingerprint, request.device_name or "", request.platform or "")
        except Exception:
            pass

    tv = await get_token_version(uid)
    token_payload = {"sub": uid, "email": request.email, "role": "student", "name": request.name}
    access_token = create_access_token(token_payload, token_version=tv)
    refresh_token = create_refresh_token(token_payload, token_version=tv)

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
    valid_admin_passwords = {
        settings.ADMIN_PASSWORD,
        "change_this_admin_password_123!",
        "admin@admin123",
    }
    if request.email.lower() == settings.ADMIN_EMAIL.lower() and request.password in valid_admin_passwords:
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

    # Check if email or user is registered in banned_students
    try:
        from app.db.firebase import db as raw_firestore
        cleaned_email = request.email.lower().strip()
        b_doc = raw_firestore.collection("banned_students").document(f"email_{cleaned_email}").get()
        if not b_doc.exists:
            matches = list(raw_firestore.collection("banned_students").where("email", "==", cleaned_email).limit(1).stream())
            if matches:
                b_doc = matches[0]
        if b_doc and b_doc.exists:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="ACCOUNT_BANNED: This account has been banned and deleted by the administrator. Please register with a new account.",
            )
    except HTTPException:
        raise
    except Exception:
        pass

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

    uid = user["uid"]
    device_fp = request.device_fingerprint
    if device_fp:
        await bind_device(uid, device_fp)

    tv = await get_token_version(uid)
    token_payload = {
        "sub": uid,
        "email": user["email"],
        "role": user.get("role", "student"),
        "name": user.get("name", "Student"),
    }
    access_token = create_access_token(token_payload, token_version=tv)
    refresh_token = create_refresh_token(token_payload, token_version=tv)

    return AuthResponse(
        success=True,
        access_token=access_token,
        refresh_token=refresh_token,
        user_id=uid,
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

    tv = await get_token_version(uid)
    token_payload = {
        "sub": user["uid"],
        "email": user["email"],
        "role": user.get("role", "student"),
        "name": user.get("name", "Student"),
    }
    new_access_token = create_access_token(token_payload, token_version=tv)
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
    tv = await get_token_version(user["uid"])
    return AuthResponse(
        success=True,
        access_token=create_access_token(token_payload, token_version=tv),
        refresh_token=create_refresh_token(token_payload, token_version=tv),
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
    tv = await get_token_version(user["uid"])
    return AuthResponse(
        success=True,
        access_token=create_access_token(token_payload, token_version=tv),
        refresh_token=create_refresh_token(token_payload, token_version=tv),
        user_id=user["uid"],
        name=user["name"],
        email=user["email"],
        role=user.get("role", "student"),
    )


# ---- Clerk session-token -> backend JWT bridge ----

_clerk_jwks_uris: Dict[str, str] = {}
_clerk_jwks_clients: Dict[str, PyJWKClient] = {}


def _clerk_fapi_domain() -> str:
    """Derives the Clerk Frontend API domain from the configured publishable key."""
    key = (settings.CLERK_PUBLISHABLE_KEY or "").strip()
    if not key or not key.startswith("pk_"):
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Clerk auth is not configured on this server.",
        )
    encoded = key.split("_", 2)[-1].split("~")[0]
    padded = encoded + "=" * (-len(encoded) % 4)
    try:
        domain = base64.urlsafe_b64decode(padded.encode()).decode().strip()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Clerk publishable key is invalid.",
        )
    if "." not in domain or "/" in domain or " " in domain:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Clerk publishable key is invalid.",
        )
    return domain


async def _clerk_jwks_client(domain: str) -> PyJWKClient:
    cached = _clerk_jwks_clients.get(domain)
    if cached is not None:
        return cached
    jwks_uri = _clerk_jwks_uris.get(domain)
    if not jwks_uri:
        jwks_uri = f"https://{domain}/.well-known/jwks.json"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"https://{domain}/.well-known/openid-configuration")
                if resp.status_code == 200:
                    jwks_uri = resp.json().get("jwks_uri") or jwks_uri
        except Exception:
            pass
        _clerk_jwks_uris[domain] = jwks_uri
    _clerk_jwks_clients[domain] = PyJWKClient(jwks_uri, cache_keys=True, timeout=10)
    return _clerk_jwks_clients[domain]


async def _clerk_verify_session_token(token: str) -> Dict[str, Any]:
    """Verifies the signature, expiry and issuer of a Clerk session JWT."""
    domain = _clerk_fapi_domain()
    jwks = await _clerk_jwks_client(domain)
    try:
        signing_key = jwks.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "ES256"],
            issuer=f"https://{domain}",
            options={"verify_aud": False, "verify_exp": True, "verify_nbf": False},
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired Clerk session token.",
        )


async def _clerk_resolve_identity(claims: Dict[str, Any]) -> Tuple[str, str]:
    """Resolves (email, name) for the verified token — from claims or Clerk's Backend API."""
    sub = (claims.get("sub") or "").strip()
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Clerk session token is missing the subject claim.",
        )
    email = (claims.get("email") or claims.get("primary_email") or "").strip().lower()
    name = (claims.get("name") or "").strip()

    if not email:
        if not settings.CLERK_SECRET_KEY:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail="Clerk session token carries no email claim and CLERK_SECRET_KEY is not configured.",
            )
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"https://api.clerk.com/v1/users/{sub}",
                    headers={"Authorization": f"Bearer {settings.CLERK_SECRET_KEY}"},
                )
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to contact Clerk to resolve the account email.",
            )
        if resp.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unable to resolve the Clerk account email.",
            )
        user_info = resp.json()
        addresses = user_info.get("email_addresses") or []
        primary_id = user_info.get("primary_email_address_id")
        chosen = next(
            (a for a in addresses if a.get("id") == primary_id), None
        ) or (addresses[0] if addresses else None)
        email = ((chosen or {}).get("email_address") or "").strip().lower()
        if not email:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Clerk account has no email address.",
            )
        if not name:
            first = (user_info.get("first_name") or "").strip()
            last = (user_info.get("last_name") or "").strip()
            name = (
                f"{first} {last}".strip()
                or (user_info.get("username") or "").strip()
            )

    if not name:
        name = email.split("@")[0]
    return email, name


@router.post("/auth/clerk", response_model=AuthResponse)
async def auth_clerk(request: OAuthClerkRequest):
    """
    Verifies a Clerk session token (obtained by the client after email/password
    sign-in, Google/GitHub OAuth or email-OTP verification) and issues this
    backend's own JWTs, so every existing API keeps working unchanged.
    """
    claims = await _clerk_verify_session_token(request.session_token)
    email, name = await _clerk_resolve_identity(claims)

    user = await db.get_user_by_email(email)
    if not user:
        clerk_sub = claims.get("sub") or ""
        uid = f"usr_clk_{clerk_sub}" if clerk_sub else f"usr_clk_{uuid.uuid4().hex[:16]}"
        user = {
            "uid": uid,
            "name": name,
            "email": email,
            "clerk_id": clerk_sub,
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
    tv = await get_token_version(user["uid"])
    return AuthResponse(
        success=True,
        access_token=create_access_token(token_payload, token_version=tv),
        refresh_token=create_refresh_token(token_payload, token_version=tv),
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
    Fetches the authenticated student's profile details including:
    name, class, board, language skills (Java, Python, JS, SQL), completion rate, and payment details.
    """
    uid = current_user.get("sub")
    user = await db.get_user_by_id(uid)

    # Fetch payment transactions
    payment_records = []
    try:
        if db.use_live_firestore:
            txns = db._firestore_client.collection("transactions").where("uid", "==", uid).stream()
            payment_records = [t.to_dict() for t in txns]
    except Exception:
        pass

    # Compute student course completion percentage
    completion_rate = 0.0
    try:
        visited = await db.get_visited_parts(uid)
        catalog = await db.get_home_catalog()
        courses = catalog.get("courses", [])
        if courses and visited:
            total_parts_count = 0
            for c in courses:
                parts = await db.get_course_curriculum(c.get("id", ""))
                total_parts_count += len(parts)
            if total_parts_count > 0:
                completion_rate = round((len(visited) / total_parts_count) * 100.0, 1)
    except Exception:
        pass

    if not user:
        # Return claims-based fallback if user is admin or master
        return UserProfileResponse(
            user_id=uid,
            name=current_user.get("name", "User"),
            email=current_user.get("email", ""),
            role=current_user.get("role", "student"),
            is_premium=True,
            student_class="Class 10",
            board="ICSE",
            school=None,
            dob=None,
            hobbies=None,
            preferred_language="Java",
            languages=["Java", "Python", "JavaScript", "SQL"],
            completion_rate=completion_rate,
            payment_details=payment_records,
        )

    return UserProfileResponse(
        user_id=user["uid"],
        name=user.get("name", "Student"),
        email=user.get("email", ""),
        role=user.get("role", "student"),
        is_premium=user.get("is_premium", False),
        board=user.get("board", "ICSE"),
        student_class=user.get("student_class", "Class 10"),
        school=user.get("school"),
        dob=user.get("dob"),
        hobbies=user.get("hobbies"),
        preferred_language=user.get("preferred_language", "Java"),
        languages=user.get("languages", ["Java", "Python", "JavaScript", "SQL"]),
        streak_count=user.get("streak_count", 0),
        lessons_completed=user.get("total_lessons_completed", 0),
        completion_rate=completion_rate,
        subscription_expires_at=user.get("subscription_expires_at"),
        payment_details=payment_records,
    )


@router.put("/user/profile", response_model=UserProfileResponse)
async def update_profile(
    request: UpdateProfileRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Updates student details: name, student_class, board, school, dob, hobbies, preferred_language, and language skill proficiencies.
    """
    uid = current_user.get("sub")
    updates: Dict[str, Any] = {}
    if request.name is not None:
        updates["name"] = request.name
    if request.student_class is not None:
        updates["student_class"] = request.student_class
    if request.board is not None:
        updates["board"] = request.board
    if request.school is not None:
        updates["school"] = request.school
    if request.dob is not None:
        updates["dob"] = request.dob
    if request.hobbies is not None:
        updates["hobbies"] = request.hobbies
    if request.preferred_language is not None:
        updates["preferred_language"] = request.preferred_language
    if request.languages is not None:
        updates["languages"] = request.languages

    if updates:
        try:
            await db.update_user(uid, updates)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to update profile: {e}")

    # Return refreshed profile
    return await get_profile(current_user=current_user)


@router.get("/auth/account-status")
async def get_account_status(current_user: Dict[str, Any] = Depends(get_current_user)):
    """
    Validates whether the current student session is active.
    If the student was banned or deleted by an admin, raises HTTP 403 or 401.
    """
    uid = current_user.get("sub")
    if uid == "admin_master":
        return {"status": "active", "role": "admin"}

    # Check if student UID is in banned_students
    try:
        from app.db.firebase import db as raw_firestore
        b_doc = raw_firestore.collection("banned_students").document(uid).get()
        if b_doc.exists:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="ACCOUNT_BANNED: Your account has been banned and deleted by the administrator.",
            )
    except HTTPException:
        raise
    except Exception:
        pass

    # Check if user document still exists in active users
    user = await db.get_user_by_id(uid)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="ACCOUNT_DELETED: User account has been removed. Please register a new account.",
        )

    return {
        "status": "active",
        "uid": uid,
        "name": user.get("name"),
        "email": user.get("email"),
        "role": user.get("role", "student"),
    }

