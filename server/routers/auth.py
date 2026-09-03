"""
Authentication router: register, login, refresh tokens, get current user.
Includes role-based access control dependencies.
"""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
import jwt
from jwt import PyJWTError as JWTError
from passlib.context import CryptContext
from fastapi.security import OAuth2PasswordBearer

from database import get_db
from config import settings
from models.user import User, ROLE_HIERARCHY
from schemas import (
    AccountDelete, UserCreate, UserLogin, UserResponse, UserUpdateRequest,
    TokenResponse, TokenRefresh, PasswordChange, MediaTokenResponse,
    MediaTokenBatchRequest, MediaTokenBatchResponse,
)
from rate_limit import (
    failed_logins,
    failed_password_changes,
    failed_refreshes,
    limiter,
)
from utils import utcnow

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
    expire = utcnow() + expires_delta
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

# Exactly the routes a user carrying `must_reset_password` may still reach:
# enough to complete the reset, or to walk away from it. Keyed on (method, path)
# rather than path alone because GET and PUT /api/auth/me are different things —
# a temporary credential has no business editing the account's email before the
# password behind it has been changed.
#
# `/api/auth/refresh` is deliberately absent and needs no entry: it validates the
# refresh token directly instead of going through this dependency. It has to keep
# working, or a session that hits the gate mid-flight could not renew the token it
# needs to complete the reset.
PASSWORD_RESET_ALLOWED_ROUTES = frozenset({
    ("GET", "/api/auth/me"),
    ("POST", "/api/auth/change-password"),
    ("POST", "/api/auth/logout"),
})


async def get_current_user(
    request: Request,
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

    # Issue #209: the flag existed but nothing server-side honoured it, so an
    # admin-issued temporary password stayed a fully working credential for any
    # client that wasn't the React app. `require_role` derives from this
    # dependency, so every role-gated route inherits the gate too.
    if (
        user.must_reset_password
        and (request.method, request.url.path) not in PASSWORD_RESET_ALLOWED_ROUTES
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="password_reset_required",
        )
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

# Hard cap on the audit `details` column at write time (issue #261). The column
# is `Text`, so nothing in the schema bounds it.
AUDIT_DETAILS_MAX_CHARS = 500


async def log_audit(
    db: AsyncSession,
    action: str,
    user_id: Optional[int] = None,
    target_user_id: Optional[int] = None,
    details: Optional[str] = None,
    ip_address: Optional[str] = None,
):
    """Record a security-relevant event in the audit log.

    `details` is truncated to AUDIT_DETAILS_MAX_CHARS here rather than at each
    call site: some of it is attacker-supplied (a failed login embeds the
    username that was typed) and the column is an uncapped `Text` — issue #261.
    """
    from models.audit_log import AuditLog
    if details is not None and len(details) > AUDIT_DETAILS_MAX_CHARS:
        details = details[: AUDIT_DETAILS_MAX_CHARS - 1] + "…"
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
    """Return the client address from the socket peer (``scope["client"]``).

    Never read X-Forwarded-For here — it is attacker-chosen. Behind the
    reverse proxy, uvicorn's ProxyHeadersMiddleware already rewrites
    ``scope["client"]`` from that header, but only when the socket peer is a
    trusted proxy (``FORWARDED_ALLOW_IPS``). See issue #156.

    Fall back to "unknown" when there is no peer or its host is empty:
    ProxyHeadersMiddleware sets the client to ``(None, 0)`` when *every* hop
    in X-Forwarded-For is trusted (real docker NAT topologies, where the
    proxy only ever sees the bridge gateway IP) — without the fallback the
    audit row stored NULL.
    """
    return request.client.host if request.client and request.client.host else "unknown"


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
    # Per-username throttle (issue #296), checked before the user is even looked
    # up: the whole point is to refuse without spending a bcrypt verify, and
    # checking before the password means a locked bucket answers 429 whatever
    # the password was — so it can never be used as a password oracle.
    retry_after = failed_logins.retry_after(credentials.username)
    if retry_after is not None:
        # Commit for the same reason as login_failed below: the raise unwinds
        # through get_db, which rolls the session back (PR #289).
        await log_audit(
            db, "login_locked",
            details=(
                f"Login temporarily locked for username '{credentials.username}' "
                f"after repeated failures"
            ),
            ip_address=get_client_ip(request),
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )

    result = await db.execute(
        select(User).where(User.username == credentials.username)
    )
    user = result.scalar_one_or_none()

    if not user or not verify_password(credentials.password, user.hashed_password):
        failed_logins.record_failure(credentials.username)
        # Log failed attempt. Commit explicitly: the 401 below propagates into
        # get_db, whose error path rolls the session back — without this the
        # login_failed row silently never persisted (issue #156).
        await log_audit(
            db, "login_failed",
            details=f"Failed login for username '{credentials.username}'",
            ip_address=get_client_ip(request),
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    # Right password: the account is not under attack, so drop its counter.
    # Done before the is_active gate — a pending user typing the correct
    # password shouldn't accumulate toward a lockout either.
    failed_logins.clear(credentials.username)

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
    # Issue #264: bound *rejected* refreshes. Each one costs a DB lookup, and
    # this endpoint is unauthenticated. Successful refreshes are not counted —
    # they are cheap, and the web client single-flights them (#268).
    #
    # Keyed by the token's subject, which is why this is here and not a
    # decorator: the token is in the request body, and slowapi key functions are
    # synchronous and cannot read it (issue #296, and the note at the top of
    # rate_limit.py). Keying on the client address instead would collapse the
    # whole deployment into one bucket behind the proxy (#294).
    def _reject() -> HTTPException:
        failed_refreshes.record_failure(body.refresh_token)
        return HTTPException(status_code=401, detail="Invalid refresh token")

    try:
        payload = jwt.decode(
            body.refresh_token, settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        user_id = payload.get("sub")
        token_type = payload.get("type")
        token_version = payload.get("ver", 0)
        retry_after = failed_refreshes.retry_after(body.refresh_token)
        if retry_after is not None:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many rejected refresh attempts. Try again later.",
                headers={"Retry-After": str(retry_after)},
            )
        if user_id is None or token_type != "refresh":
            raise _reject()
    except JWTError:
        # Undecodable: no subject to key on, and nothing was looked up in the
        # database either, so there is nothing here worth counting.
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    result = await db.execute(select(User).where(User.id == int(user_id)))
    user = result.scalar_one_or_none()
    if not user or not user.is_active or token_version != user.token_version:
        raise _reject()

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
    # Issue #264. This endpoint verifies the current password, so without a
    # bound it is an online brute-force oracle against bcrypt for anyone holding
    # a stolen access token — and succeeding here bumps token_version, locking
    # the real user out of their own account.
    #
    # Checked before the password, like the login lockout, so a locked bucket
    # answers 429 whatever was submitted and can never be used as an oracle.
    user_key = str(current_user.id)
    retry_after = failed_password_changes.retry_after(user_key)
    if retry_after is not None:
        await log_audit(
            db, "password_change_locked", user_id=current_user.id,
            details="Password change temporarily locked after repeated failures",
            ip_address=get_client_ip(request),
        )
        # Commit explicitly: the raise unwinds through get_db, which rolls the
        # session back, and the audit row would silently never persist (#156).
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )

    if not verify_password(body.old_password, current_user.hashed_password):
        failed_password_changes.record_failure(user_key)
        await log_audit(
            db, "password_change_failed", user_id=current_user.id,
            details="Failed password change: current password incorrect",
            ip_address=get_client_ip(request),
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    # Correct password: the run of failures is over.
    failed_password_changes.clear(user_key)

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


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_me(
    body: AccountDelete,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete the caller's own account and everything belonging to it (#146).

    Google Play requires an in-app deletion path for any app that can create an
    account. The only delete before this was ``DELETE /api/users/{id}``, which
    is admin-gated and refuses the caller's own row — so on a self-hosted
    server the only way out was to ask the operator.

    What goes: the user, their bookmarks (and each bookmark's log rows and
    per-device position hints) and their progress rows, via the
    ``all, delete-orphan`` ORM cascades on ``User`` and ``Bookmark`` backed by
    ``ON DELETE CASCADE`` on the FKs themselves (migrations 0011 and 0013).

    What stays: the audit trail. ``audit_logs.user_id`` is ``ON DELETE SET
    NULL``, so the row written just below outlives the account with its actor
    redacted, while ``target_user_id`` — which carries no FK — keeps the number
    an operator needs to make sense of it.

    Refusals:

    * wrong password -> 403. Rate-limited through the same per-user bucket as
      change-password (#264): both verify the current password, so both are the
      same bcrypt oracle for anyone holding a stolen access token, and here the
      payoff is destruction rather than takeover.
    * last active superadmin -> 409. Not a permission problem — the caller is
      the most privileged account there is — but a deployment with no active
      superadmin cannot approve a registration, promote anyone or open the
      admin console, and nothing in the app can undo it.
    """
    user_key = str(current_user.id)
    retry_after = failed_password_changes.retry_after(user_key)
    if retry_after is not None:
        await log_audit(
            db, "account_delete_locked", user_id=current_user.id,
            details="Account deletion temporarily locked after repeated failures",
            ip_address=get_client_ip(request),
        )
        # Commit explicitly: the raise unwinds through get_db, which rolls the
        # session back, and the audit row would silently never persist (#156).
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )

    if not verify_password(body.password, current_user.hashed_password):
        failed_password_changes.record_failure(user_key)
        await log_audit(
            db, "account_delete_failed", user_id=current_user.id,
            details="Failed account deletion: password incorrect",
            ip_address=get_client_ip(request),
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Password is incorrect",
        )

    failed_password_changes.clear(user_key)

    if current_user.role == "superadmin":
        remaining = (await db.execute(
            select(func.count()).select_from(User).where(
                User.role == "superadmin",
                User.is_active.is_(True),
                User.id != current_user.id,
            )
        )).scalar_one()
        if remaining == 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "You are the last active superadmin. Promote another user to "
                    "superadmin first, or this server would be left with nobody "
                    "who can approve accounts or reach the admin console."
                ),
            )

    user_id = current_user.id
    username = current_user.username

    # Written *before* the delete on purpose: the FK's ON DELETE SET NULL is
    # what redacts `user_id`, so the row records the event and then stops
    # naming an account that no longer exists.
    await log_audit(
        db, "account_self_deleted", user_id=user_id, target_user_id=user_id,
        details=f"User '{username}' deleted their own account",
        ip_address=get_client_ip(request),
    )

    await db.delete(current_user)
    await db.flush()

    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
