"""Single-use registration invites (issue #210).

An invite is what makes ``registration_mode = "invite"`` mean anything: an admin
generates one, hands the code to the person who should have an account, and the
register endpoint accepts that one request and no others.

The row stores a **hash** of the code, never the code — the same decision as
``refresh_tokens.jti``. A leaked copy of this table cannot be turned back into a
working invite, and the code exists in plaintext exactly once, in the response to
the admin who created it. ``sha256`` rather than bcrypt because the code is 128
bits of ``secrets.token_urlsafe`` output, not a human-chosen password: there is
nothing to brute-force, and the lookup has to be a single indexed equality so the
claim can be one atomic conditional UPDATE (see ``services/invites.py``).
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base
from utils import utcnow


class Invite(Base):
    """One admin-issued, single-use, expiring registration code."""

    __tablename__ = "invites"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # sha256 hex of the code. Unique because it is the invite's identity, and
    # indexed because the register path looks up by nothing else.
    code_hash: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    # SET NULL rather than CASCADE on both user columns: deleting the admin who
    # issued an invite, or the account that redeemed one, must not delete the
    # record that it happened — the same reasoning as `audit_logs.user_id`.
    created_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # Set by the claim. Non-NULL is what makes the invite spent, and the claim's
    # `WHERE used_at IS NULL` is what makes "spent" impossible to race.
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    used_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    def __repr__(self) -> str:
        return f"<Invite(id={self.id}, used={self.used_at is not None})>"
