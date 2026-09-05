"""Creating and spending registration invites (issue #210).

The code itself never touches the database — only its ``sha256``. It exists in
plaintext for the length of one response (``POST /api/auth/invites``) and, later,
one request (``POST /api/auth/register``). Nothing logs it, including the audit
rows, which name the invite by ``id``.

**Claiming is one conditional UPDATE.** Reading the row, deciding it is unused,
and then marking it used is a race two simultaneous registrations can both win —
which turns a single-use invite into an unlimited one for anyone who can send two
requests at once. ``UPDATE … WHERE code_hash = ? AND used_at IS NULL AND
expires_at > ?`` cannot: the database serialises it, exactly one statement
reports a row, and the loser sees zero.
"""

import hashlib
import logging
import secrets
from datetime import timedelta
from typing import Optional, Tuple

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.invite import Invite
from services import registration
from utils import utcnow

logger = logging.getLogger(__name__)

#: 24 bytes of entropy, url-safe — 32 characters, no ambiguity about how to type
#: it, and far past anything guessable at the register endpoint's rate limit.
CODE_BYTES = 24


def generate_code() -> str:
    return secrets.token_urlsafe(CODE_BYTES)


def hash_code(code: str) -> str:
    """The stored form. Plain sha256: the input is machine-generated entropy,
    not a password, so a work factor would buy nothing and would forbid the
    indexed equality the atomic claim depends on."""
    return hashlib.sha256(str(code).encode("utf-8")).hexdigest()


async def create_invite(
    db: AsyncSession,
    created_by_user_id: Optional[int] = None,
    expiry_days: Optional[int] = None,
) -> Tuple[Invite, str]:
    """Mint an invite. Returns the row and the plaintext code — the only time
    the code exists outside the caller's hands."""
    if expiry_days is None:
        expiry_days = int(registration.current("invite_expiry_days"))
    code = generate_code()
    invite = Invite(
        code_hash=hash_code(code),
        created_by_user_id=created_by_user_id,
        created_at=utcnow(),
        expires_at=utcnow() + timedelta(days=max(1, int(expiry_days))),
    )
    db.add(invite)
    await db.flush()
    return invite, code


async def consume_invite(db: AsyncSession, code: Optional[str]) -> Optional[int]:
    """Spend ``code`` and return the invite's id, or ``None`` if it cannot be.

    ``None`` covers every refusal — no code sent, a code nobody issued, one
    already spent, one past its expiry, one an admin revoked — because the caller
    must answer all of them identically anyway.
    """
    if not code:
        return None
    result = await db.execute(
        update(Invite)
        .where(
            Invite.code_hash == hash_code(code),
            Invite.used_at.is_(None),
            Invite.expires_at > utcnow(),
        )
        .values(used_at=utcnow())
        .returning(Invite.id)
        .execution_options(synchronize_session=False)
    )
    return result.scalar_one_or_none()


async def attach_user(db: AsyncSession, invite_id: int, user_id: int) -> None:
    """Record which account a spent invite produced.

    Separate from the claim on purpose: the claim happens before the account
    exists, because it is what decides whether an account may be created at all.
    """
    await db.execute(
        update(Invite)
        .where(Invite.id == invite_id)
        .values(used_by_user_id=user_id)
        .execution_options(synchronize_session=False)
    )


async def list_invites(db: AsyncSession) -> list[Invite]:
    """Newest first. Never carries the code — there is none to carry."""
    return list(
        (
            await db.execute(select(Invite).order_by(Invite.id.desc()))
        ).scalars().all()
    )


def status_of(invite: Invite) -> str:
    """``used`` | ``expired`` | ``active`` — what the admin list shows."""
    if invite.used_at is not None:
        return "used"
    if invite.expires_at <= utcnow():
        return "expired"
    return "active"
