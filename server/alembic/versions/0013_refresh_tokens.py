"""per-device refresh sessions (issue #250)

Logging out bumped ``users.token_version``, which invalidated every token the
account held on every device: signing out of the browser signed out the phone.
For a reading app that is not cosmetic — the phone carries unsynced positions
that its startup reconcile and 15-minute sweep push later, and after a remote
logout those pushes 401 until somebody notices and signs in again.

This table gives each signed-in device a session. The refresh token carries the
row's ``jti``; the access and media tokens minted from it carry the same value
as ``sid``. ``POST /api/auth/logout`` marks one row revoked, so it signs out one
device. ``token_version`` keeps its old job as the account-wide kill switch, now
used only where that is what is wanted: password change, admin reset, and the
explicit ``POST /api/auth/logout-all``.

Nothing here backfills, and nothing needs to. A token issued before this
migration carries no ``jti``/``sid``: the server keeps accepting it on ``ver``
alone (exactly the rule it was issued under), and the first refresh hands that
device a session-backed pair. So the deploy signs nobody out.

Additive DDL only — one new table and its two indexes. No ``ALTER``, so no
batch mode is needed for the SQLite branch; ``create_table`` and
``create_index`` are portable as written.

Revision ID: 0013_refresh_tokens
Revises: 0012_book_path_indexes
Create Date: 2026-09-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
# NOTE: keep this at or under 32 chars — see the note in
# 0002_conflict_resolution.py (alembic_version is a VARCHAR(32)).
revision: str = "0013_refresh_tokens"
down_revision: Union[str, None] = "0012_book_path_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        # The session's name, not the token: the JWT itself is never stored, so
        # a copy of this table cannot be turned into a working credential.
        sa.Column("jti", sa.String(length=64), nullable=False),
        # The client's install id (`getDeviceId()` on web, `DeviceIdManager` on
        # Android). Nullable because a client that does not send one still gets
        # a session — it just cannot be named.
        sa.Column("device_id", sa.String(length=100), nullable=True),
        sa.Column("issued_at", sa.DateTime(), nullable=False),
        # Bumped on every refresh, so login can prune sessions that have gone
        # past JWT_REFRESH_TOKEN_EXPIRE_DAYS without evicting a device that is
        # still using its session.
        sa.Column("last_used_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        # CASCADE, like `bookmarks` and `user_progress` since migration 0011: a
        # session must never outlive the account it authenticates, and it must
        # never be the reason an account cannot be deleted.
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # Unique, because the jti is the session's identity and every lookup on the
    # auth path goes through it.
    op.create_index(
        op.f("ix_refresh_tokens_jti"), "refresh_tokens", ["jti"], unique=True
    )
    # Read by "revoke everything for this user" (password change, admin reset,
    # logout-all) and by the prune on login.
    op.create_index(
        op.f("ix_refresh_tokens_user_id"), "refresh_tokens", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_refresh_tokens_user_id"), table_name="refresh_tokens")
    op.drop_index(op.f("ix_refresh_tokens_jti"), table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
