"""
Authentication router: register, login, refresh tokens, get current user.
Includes role-based access control dependencies.
"""

import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
import jwt
from jwt import PyJWTError as JWTError
from passlib.context import CryptContext
from fastapi.security import OAuth2PasswordBearer

from database import get_db
from config import settings
from models.refresh_token import RefreshToken
from models.user import User, ROLE_HIERARCHY
from schemas import (
    UserCreate, UserLogin, UserResponse, UserUpdateRequest, TokenResponse, TokenRefresh,
    PasswordChange, LogoutRequest, LogoutResponse,
    MediaTokenResponse, MediaTokenBatchRequest, MediaTokenBatchResponse,
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


def create_access_token(user: User, session_jti: Optional[str] = None) -> str:
    """Create an access token, bound to the user's current token_version and —
    when the caller has one — to their session (issue #250).

    `session_jti` is optional on purpose. A token minted without it is exactly
    the shape of every access token this deployment had already handed out
    before per-device sessions existed, and `get_current_user` goes on
    accepting those on `ver` alone until they expire.
    """
    claims = {"sub": str(user.id), "type": "access", "ver": user.token_version}
    if session_jti:
        claims["sid"] = session_jti
    return create_token(
        claims, timedelta(minutes=settings.jwt_access_token_expire_minutes)
    )


def create_refresh_token(user: User, session_jti: Optional[str] = None) -> str:
    """Create a refresh token, bound to the user's current token_version and,
    when given, to the session it renews (issue #250)."""
    claims = {"sub": str(user.id), "type": "refresh", "ver": user.token_version}
    if session_jti:
        claims["jti"] = session_jti
    return create_token(
        claims, timedelta(days=settings.jwt_refresh_token_expire_days)
    )


def create_media_token(
    user: User,
    resource_type: str,
    resource_id: str,
    session_jti: Optional[str] = None,
) -> str:
    """Create a short-lived, resource-scoped token for cover/audio URLs that
    can't carry an Authorization header (img tags, Cast SDK media URLs).

    Carries the minting session so it dies with it. Before issue #250 the
    `token_version` bump on logout killed these; a per-device logout does not
    bump it, so without `sid` a signed-out browser would go on streaming covers
    and audio for the rest of the token's lifetime — the shape of issue #206.
    """
    claims = {
        "sub": str(user.id),
        "type": "media",
        "resource_type": resource_type,
        "resource_id": resource_id,
        "ver": user.token_version,
    }
    if session_jti:
        claims["sid"] = session_jti
    return create_token(
        claims, timedelta(minutes=settings.jwt_media_token_expire_minutes)
    )


# ---------------------------------------------------------------------------
# Refresh sessions (issue #250)
#
# One `refresh_tokens` row per signed-in device. The refresh token carries its
# `jti`; the access and media tokens minted from it carry the same value as
# `sid`. Logging out revokes one row, so it signs out one device — which is the
# whole point: this app's other device is usually mid-book with unsynced
# positions, and the old global revoke stopped its background pushes until
# somebody noticed and signed in again.
#
# `token_version` keeps its old job as the account-wide kill switch, used by
# password change, admin reset, and the explicit `/logout-all`.
# ---------------------------------------------------------------------------


def _new_session_jti() -> str:
    """A session's name: 32 url-safe random characters, derived from nothing."""
    return secrets.token_urlsafe(24)


async def session_is_live(db: AsyncSession, jti: str, user_id: int) -> bool:
    """True if `jti` names a session of `user_id` that has not been revoked.

    Public because `routers/files.py` resolves its tokens without going through
    `get_current_user` and has to make the same check there (issue #206).
    """
    result = await db.execute(
        select(RefreshToken.id).where(
            RefreshToken.jti == jti,
            RefreshToken.user_id == user_id,
            RefreshToken.revoked_at.is_(None),
        )
    )
    return result.scalar_one_or_none() is not None


async def open_session(
    db: AsyncSession, user: User, device_id: Optional[str] = None
) -> RefreshToken:
    """Start a session for one device and return its row."""
    now = utcnow()
    # Sessions whose refresh token has outlived JWT_REFRESH_TOKEN_EXPIRE_DAYS can
    # never authenticate again — the JWT's own `exp` refuses them — so the rows
    # are dead weight. Pruning on the route that creates them keeps the table
    # bounded with no scheduled job to forget about.
    cutoff = now - timedelta(days=settings.jwt_refresh_token_expire_days)
    await db.execute(
        delete(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.last_used_at < cutoff)
        .execution_options(synchronize_session=False)
    )
    if device_id:
        # One live session per device: signing in again on a device supersedes
        # the session that device already had, rather than stacking a second one
        # that nothing will ever revoke. Sessions with no device id cannot be
        # matched up this way and are simply left alone.
        await db.execute(
            update(RefreshToken)
            .where(
                RefreshToken.user_id == user.id,
                RefreshToken.device_id == device_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=now)
            .execution_options(synchronize_session=False)
        )
    session = RefreshToken(
        user_id=user.id,
        jti=_new_session_jti(),
        device_id=device_id,
        issued_at=now,
        last_used_at=now,
    )
    db.add(session)
    await db.flush()
    return session


async def revoke_all_sessions(db: AsyncSession, user_id: int) -> None:
    """Revoke every live session of a user. Always paired with a
    `token_version` bump — the bump is what invalidates the tokens already in
    flight; this is what stops the sessions behind them being reusable."""
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
        .execution_options(synchronize_session=False)
    )


def _session_named_by(token: str, user: User) -> Optional[str]:
    """The session a client-supplied refresh token names, or None if the token
    cannot name one: undecodable, expired, not a refresh token, not this user's,
    or old enough to predate sessions."""
    try:
        payload = jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
    except JWTError:
        return None
    if payload.get("type") != "refresh" or payload.get("sub") != str(user.id):
        return None
    return payload.get("jti")


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
    ("POST", "/api/auth/logout-all"),
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

    # Session binding (issue #250). A token carrying no `sid` predates
    # per-device sessions and is accepted on `ver` alone, exactly as it was when
    # it was issued; one that names a session is only as alive as that session,
    # which is what makes "sign out this device" mean it — including for the
    # access token the device is holding, which the old global bump killed as a
    # side effect and which must not quietly outlive logout now.
    session_jti = payload.get("sid")
    if session_jti is not None and not await session_is_live(db, session_jti, user.id):
        raise credentials_exception
    # Read by the media-token routes so the tokens they mint belong to the same
    # session, and by `logout` so a client that sends no body still signs out
    # only itself.
    request.state.session_id = session_jti

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
    """Factory that returns a FastAPI dependency requiring at least `minimum_role`.

    Raises `ValueError` for a floor that is not a real role (issue #359). The
    check is here, in the factory, rather than in the dependency: the three
    aliases below are built at import, so a typo is a startup crash the deploy
    cannot get past. The old `ROLE_HIERARCHY.get(minimum_role, 0)` scored an
    unrecognised floor as 0 and everybody clears 0 — `require_role("editorr")`
    returned a working dependency that admitted plain users, with no log line
    and no 500 to notice it by.
    """
    if minimum_role not in ROLE_HIERARCHY:
        raise ValueError(
            f"unknown role floor {minimum_role!r}; expected one of "
            f"{sorted(ROLE_HIERARCHY)}"
        )

    async def dependency(current_user: User = Depends(get_current_user)) -> User:
        user_level = ROLE_HIERARCHY.get(current_user.role, 0)
        required_level = ROLE_HIERARCHY[minimum_role]
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

    session = await open_session(db, user, credentials.device_id)

    return TokenResponse(
        access_token=create_access_token(user, session.jti),
        refresh_token=create_refresh_token(user, session.jti),
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

    # Per-device sessions (issue #250).
    session_jti = payload.get("jti")
    if session_jti is None:
        # A refresh token minted before sessions existed. It was valid when it
        # was issued and its `ver` still checks out, so honour it — no client is
        # signed out by the deploy — and hand back a session-backed pair, which
        # upgrades that device on its first refresh.
        session = await open_session(db, user, body.device_id)
    else:
        session = (await db.execute(
            select(RefreshToken).where(
                RefreshToken.jti == session_jti,
                RefreshToken.user_id == user.id,
                RefreshToken.revoked_at.is_(None),
            )
        )).scalar_one_or_none()
        if session is None:
            raise _reject()
        session.last_used_at = utcnow()
        if body.device_id and not session.device_id:
            # An upgraded client naming itself for the first time.
            session.device_id = body.device_id

    # The session keeps its `jti`: the new refresh token renews the expiry
    # without killing the one that was presented.
    #
    # Invalidating it here — true rotation — would make a refresh whose response
    # never arrives terminal, because the client would still hold the old token
    # and the server would have already retired it. Both clients single-flight
    # their refresh (web `refreshSession`, Android `TokenAuthenticator`), which
    # handles concurrent 401s but not a lost reply, a killed process, or a
    # restore from backup. Revocation, not use, is what ends a session, so a
    # copy of the refresh token is worth no more than it was before this change
    # — and `logout` now kills it on the device it was taken from.
    return TokenResponse(
        access_token=create_access_token(user, session.jti),
        refresh_token=create_refresh_token(user, session.jti),
    )


VALID_MEDIA_RESOURCE_TYPES = {"cover", "audiobook"}


@router.get("/media-token", response_model=MediaTokenResponse)
async def get_media_token(
    request: Request,
    resource_type: str,
    resource_id: str,
    current_user: User = Depends(get_current_user),
):
    """Mint a short-lived token scoped to one cover/audiobook resource, for
    use in URLs that can't carry an Authorization header (img tags, Cast)."""
    if resource_type not in VALID_MEDIA_RESOURCE_TYPES:
        raise HTTPException(status_code=400, detail="Invalid resource_type")
    token = create_media_token(
        current_user, resource_type, resource_id,
        session_jti=getattr(request.state, "session_id", None),
    )
    return MediaTokenResponse(
        token=token,
        expires_in=settings.jwt_media_token_expire_minutes * 60,
    )


@router.post("/media-token/batch", response_model=MediaTokenBatchResponse)
async def get_media_tokens_batch(
    request: Request,
    body: MediaTokenBatchRequest,
    current_user: User = Depends(get_current_user),
):
    """Mint scoped media tokens for multiple resources in one round-trip
    (e.g. all covers visible on a library grid page)."""
    session_jti = getattr(request.state, "session_id", None)
    tokens = {}
    for r in body.resources[:200]:
        if r.resource_type not in VALID_MEDIA_RESOURCE_TYPES or not r.resource_id:
            continue
        key = f"{r.resource_type}:{r.resource_id}"
        tokens[key] = create_media_token(
            current_user, r.resource_type, r.resource_id, session_jti=session_jti,
        )
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
    # Global, and deliberately so: a password change is the case where every
    # device has to go, whatever session asked for it (issue #250).
    current_user.token_version += 1
    await revoke_all_sessions(db, current_user.id)
    await db.flush()

    await log_audit(
        db, "password_changed", user_id=current_user.id,
        details="User changed their own password",
        ip_address=get_client_ip(request),
    )

    return {"message": "Password changed successfully"}


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    request: Request,
    body: Optional[LogoutRequest] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Sign this device out, leaving the account's other devices signed in
    (issue #250).

    The session to end is named by the refresh token in the body if the client
    sends one, and otherwise by the access token that authenticated the call —
    so a client that has not been updated still gets a per-device logout.

    The fallback is the old behaviour, `token_version + 1`, and it fires only
    when there is no session to name at all: a token issued before this existed.
    That is the conservative answer, not a leftover — an old client cannot tell
    the user "only this device", so it must not silently mean less than the
    sign-out it was built to perform. `POST /logout-all` is the explicit way to
    ask for it.

    Deliberately idempotent: when the named session is already revoked this
    still answers 200 without touching `token_version`. Falling back to the
    global revoke there would turn a retried logout — a flaky network, a double
    tap — into exactly the "signed out everywhere" this endpoint stopped doing.
    """
    session_jti = _session_named_by(body.refresh_token, current_user) if body else None
    if session_jti is None:
        session_jti = getattr(request.state, "session_id", None)

    if session_jti is not None:
        session = (await db.execute(
            select(RefreshToken).where(
                RefreshToken.jti == session_jti,
                RefreshToken.user_id == current_user.id,
            )
        )).scalar_one_or_none()
        if session is not None and session.revoked_at is None:
            session.revoked_at = utcnow()
        scope = "device"
    else:
        current_user.token_version += 1
        await revoke_all_sessions(db, current_user.id)
        scope = "all"
    await db.flush()

    await log_audit(
        db, "logout", user_id=current_user.id,
        details=f"User logged out ({scope})",
        ip_address=get_client_ip(request),
    )

    return LogoutResponse(message="Logged out", scope=scope)


@router.post("/logout-all", response_model=LogoutResponse)
async def logout_all(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Sign out on every device — the behaviour `logout` had before issue #250,
    kept as something the user asks for rather than something that happens to
    them. This is the button to reach for when a device has been lost."""
    current_user.token_version += 1
    await revoke_all_sessions(db, current_user.id)
    await db.flush()

    await log_audit(
        db, "logout_all", user_id=current_user.id,
        details="User signed out on all devices",
        ip_address=get_client_ip(request),
    )

    return LogoutResponse(message="Signed out on all devices", scope="all")
