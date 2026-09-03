"""
Per-device refresh sessions (issue #250).

One row per signed-in device. The refresh JWT carries the row's ``jti``; the
access and media tokens minted from it carry the same value as ``sid``. That is
what makes "sign out" mean *this* device rather than every device the account
has ever signed in on: ``logout`` marks one row revoked, and every token minted
for that session stops being accepted, while the other devices keep theirs.

The row stores no token — only the identifier inside it. A leaked copy of this
table still cannot be turned into a working token, because the signature depends
on ``JWT_SECRET_KEY``.

``token_version`` on ``users`` is untouched by a per-device logout and remains
the account-wide kill switch: password change, admin reset, and the explicit
``POST /api/auth/logout-all`` all bump it, which invalidates every token of
every session at once.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base
from utils import utcnow


class RefreshToken(Base):
    """A single device's refresh session."""

    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # CASCADE at the database level, and deliberately no ORM relationship:
    # deleting an account must not be blocked by its sessions, and a session
    # must never outlive the user it authenticates (the same decision, and the
    # same reasoning, as migration 0011).
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The `jti` claim of the refresh token, and the `sid` claim of every access
    # and media token minted from it. Unique because it is the session's name.
    jti: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    # The client's own install id (`getDeviceId()` on web, `DeviceIdManager` on
    # Android). Nullable: a client that predates this — or one that simply does
    # not send it — still gets a session, it just cannot be named. Never trusted
    # for authorization; the `jti` is what authenticates.
    device_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    # Bumped on every refresh, which is what makes pruning safe: a session stays
    # in the table for as long as it is still being used, and `issued_at` keeps
    # meaning "when this device signed in".
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<RefreshToken(user={self.user_id}, device={self.device_id!r}, "
            f"revoked={self.revoked_at is not None})>"
        )
