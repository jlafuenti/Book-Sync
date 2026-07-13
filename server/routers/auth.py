"""
Authentication router: register, login, refresh tokens, get current user.
Includes role-based access control dependencies.
"""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi.security import OAuth2PasswordBearer

from database import get_db
from config import settings
from models.user import User, ROLE_HIERARCHY
from schemas import (
    UserCreate, UserLogin, UserResponse, UserUpdateRequest, TokenResponse, TokenRefresh,
    PasswordChange, MediaTokenResponse, MediaTokenBatchRequest, MediaTokenBatchResponse,
)
from rate_limit import limiter

router = APIRouter(prefix="/api/auth", tags=["auth"])

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def hash_password(password: str) -> str:
    """Hash a plaintext password."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a hash."""
    return pwd_context.verify(plain_password, hashed_password)


def create_token(data: dict, expires_delta: timedelta) -> str:
    """Create a JWT token with expiration."""
    to_encode = data.copy()
    expire = datetime.utcnow() + expires_delta
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(user: User) -> str:
    """Create an access token for a user, bound to their current token_version."""
    return create_token(
        {"sub": str(user.id), "type": "access", "ver": user.token_version},
        timedelta(minutes=settings.jwt_access_token_expire_minutes),
    )


def create_refresh_token(user: User) -> str:
    """Create a refresh token for a user, bound to their current token_version."""
    return create_token(
        {"sub": str(user.id), "type": "refresh", "ver": user.token_version},
        timedelta(days=settings.jwt_refresh_token_expire_days),
    )


def create_media_token(user: User, resource_type: str, resource_id: str) -> str:
    """Create a short-lived, resource-scoped token for cover/audio URLs that
    can't carry an Authorization header (img tags, Cast SDK media URLs)."""
    return create_token(
        {
            "sub": str(user.id),
            "type": "media",
            "resource_type": resource_type,
            "resource_id": resource_id,
            "ver": user.token_version,
        },
        timedelta(minutes=settings.jwt_media_token_expire_minutes),
    )


# ---------------------------------------------------------------------------
# Auth dependencies
# ---------------------------------------------------------------------------

async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """FastAPI dependency: extract and validate the current user from JWT."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
        user_id: Optional[str] = payload.get("sub")
        token_type: Optional[str] = payload.get("type")
        token_version = payload.get("ver", 0)
        if user_id is None or token_type != "access":
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    result = await db.execute(select(User).where(User.id == int(user_id)))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active or token_version != user.token_version:
        raise credentials_exception
    return user


def require_role(minimum_role: str):
    """Factory that returns a FastAPI dependency requiring at least `minimum_role`."""
    async def dependency(current_user: User = Depends(get_current_user)) -> User:
        user_level = ROLE_HIERARCHY.get(current_user.role, 0)
        required_level = ROLE_HIERARCHY.get(minimum_role, 0)
        if user_level < required_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires {minimum_role} role or higher",
            )
        return current_user
    return dependency


# Convenience aliases
get_superadmin_user = require_role("superadmin")
get_admin_user = require_role("admin")
get_editor_user = require_role("editor")


# ---------------------------------------------------------------------------
# Audit log helper
# ---------------------------------------------------------------------------

async def log_audit(
    db: AsyncSession,
    action: str,
    user_id: Optional[int] = None,
    target_user_id: Optional[int] = None,
    details: Optional[str] = None,
    ip_address: Optional[str] = None,
):
    """Record a security-relevant event in the audit log."""
    from models.audit_log import AuditLog
    entry = AuditLog(
        user_id=user_id,
        action=action,
        target_user_id=target_user_id,
        details=details,
        ip_address=ip_address,
    )
    db.add(entry)
    await db.flush()


def get_client_ip(request: Request) -> str:
    """Extract client IP from request, respecting X-Forwarded-For."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/register", status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def register(user_data: UserCreate, request: Request, db: AsyncSession = Depends(get_db)):
    """Submit an access request. Account must be approved by an admin before login."""
    if not settings.allow_public_registration:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Public registration is disabled",
        )

    # Check for existing username
    result = await db.execute(select(User).where(User.username == user_data.username))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already taken",
        )

    # Check for existing email
    result = await db.execute(select(User).where(User.email == user_data.email))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    # Create user as inactive (pending approval)
    user = User(
        username=user_data.username,
        email=user_data.email,
        hashed_password=hash_password(user_data.password),
        role="user",
        is_active=False,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)

    await log_audit(
        db, "register_request", user_id=user.id,
        details=f"Access request from '{user.username}'",
        ip_address=get_client_ip(request),
    )

    return {"message": "Access request submitted. An admin must approve your account before you can sign in."}


@router.post("/login", response_model=TokenResponse)
@limiter.limit("5/minute")
async def login(credentials: UserLogin, request: Request, db: AsyncSession = Depends(get_db)):
    """Authenticate a user and return JWT tokens."""
    result = await db.execute(
        select(User).where(User.username == credentials.username)
    )
    user = result.scalar_one_or_none()

    if not user or not verify_password(credentials.password, user.hashed_password):
        # Log failed attempt
        await log_audit(
            db, "login_failed",
            details=f"Failed login for username '{credentials.username}'",
            ip_address=get_client_ip(request),
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is pending admin approval",
        )

    await log_audit(
        db, "login", user_id=user.id,
        details=f"Successful login",
        ip_address=get_client_ip(request),
    )

    return TokenResponse(
        access_token=create_access_token(user),
        refresh_token=create_refresh_token(user),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(body: TokenRefresh, db: AsyncSession = Depends(get_db)):
    """Refresh an access token using a valid refresh token."""
    try:
        payload = jwt.decode(
            body.refresh_token, settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        user_id = payload.get("sub")
        token_type = payload.get("type")
        token_version = payload.get("ver", 0)
        if user_id is None or token_type != "refresh":
            raise HTTPException(status_code=401, detail="Invalid refresh token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    result = await db.execute(select(User).where(User.id == int(user_id)))
    user = result.scalar_one_or_none()
    if not user or not user.is_active or token_version != user.token_version:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    return TokenResponse(
        access_token=create_access_token(user),
        refresh_token=create_refresh_token(user),
    )


VALID_MEDIA_RESOURCE_TYPES = {"cover", "audiobook"}


@router.get("/media-token", response_model=MediaTokenResponse)
async def get_media_token(
    resource_type: str,
    resource_id: str,
    current_user: User = Depends(get_current_user),
):
    """Mint a short-lived token scoped to one cover/audiobook resource, for
    use in URLs that can't carry an Authorization header (img tags, Cast)."""
    if resource_type not in VALID_MEDIA_RESOURCE_TYPES:
        raise HTTPException(status_code=400, detail="Invalid resource_type")
    token = create_media_token(current_user, resource_type, resource_id)
    return MediaTokenResponse(
        token=token,
        expires_in=settings.jwt_media_token_expire_minutes * 60,
    )


@router.post("/media-token/batch", response_model=MediaTokenBatchResponse)
async def get_media_tokens_batch(
    body: MediaTokenBatchRequest,
    current_user: User = Depends(get_current_user),
):
    """Mint scoped media tokens for multiple resources in one round-trip
    (e.g. all covers visible on a library grid page)."""
    tokens = {}
    for r in body.resources[:200]:
        if r.resource_type not in VALID_MEDIA_RESOURCE_TYPES or not r.resource_id:
            continue
        key = f"{r.resource_type}:{r.resource_id}"
        tokens[key] = create_media_token(current_user, r.resource_type, r.resource_id)
    return MediaTokenBatchResponse(
        tokens=tokens,
        expires_in=settings.jwt_media_token_expire_minutes * 60,
    )


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """Get the currently authenticated user's profile."""
    return current_user


VALID_THEMES = {"blueprint", "forest-night", "ember", "aurora", "slate"}


@router.put("/me", response_model=UserResponse)
async def update_me(
    body: UserUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update the current user's profile preferences."""
    if body.theme is not None:
        if body.theme not in VALID_THEMES:
            raise HTTPException(status_code=400, detail="Invalid theme")
        current_user.theme = body.theme
    await db.flush()
    await db.refresh(current_user)
    return current_user


@router.post("/change-password")
async def change_password(
    body: PasswordChange,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Change the current user's password. Clears must_reset_password flag."""
    if not verify_password(body.old_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    current_user.hashed_password = hash_password(body.new_password)
    current_user.must_reset_password = False
    current_user.token_version += 1
    await db.flush()

    await log_audit(
        db, "password_changed", user_id=current_user.id,
        details="User changed their own password",
        ip_address=get_client_ip(request),
    )

    return {"message": "Password changed successfully"}


@router.post("/logout")
async def logout(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Log out. Bumps token_version, invalidating all outstanding tokens for
    this user (all devices — there is no per-session token store)."""
    current_user.token_version += 1
    await db.flush()

    await log_audit(
        db, "logout", user_id=current_user.id,
        details="User logged out",
        ip_address=get_client_ip(request),
    )

    return {"message": "Logged out"}
