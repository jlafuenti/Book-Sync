"""single-use registration invites (issue #210)

Self-registration was on by default, unbounded, and it answered
``409 Username already taken`` / ``409 Email already registered`` — an oracle for
which accounts exist, on a host that is about to be public. The register endpoint
now answers one neutral 201 whatever it decides, and *whether* it accepts anyone
at all is a ``registration_mode`` setting: ``open``, ``invite`` or ``closed``.

This table is what makes ``invite`` mean something. One row per code an admin
generated: single-use, expiring, and holding a ``sha256`` of the code rather than
the code, so a copy of the table cannot be turned back into a working invite.

``used_at`` carries the whole of "spent". The claim is a single conditional
``UPDATE … WHERE code_hash = ? AND used_at IS NULL AND expires_at > ?``, which is
why there is no separate status column to fall out of step with it and no
read-then-write window for two simultaneous registrations to both win.

Both user references are ``ON DELETE SET NULL``, not CASCADE: deleting the admin
who issued an invite, or the account that redeemed one, must not delete the
record that it happened — the same decision as ``audit_logs.user_id``.

Nothing is backfilled and nothing needs to be. ``registration_mode`` is seeded at
startup rather than here, because the value depends on whether the database
already has users (``services/registration.seed_mode``): an existing install is
seeded ``open`` so the upgrade changes no behaviour, a fresh one ``invite``. A
migration cannot make that call — it runs before the superadmin bootstrap on a
fresh install and after it on a rebuild, so "are there users yet" means different
things depending on how the container was started.

Additive DDL only — one new table and its index. No ``ALTER``, so no batch mode
is needed; ``create_table`` and ``create_index`` are portable as written.

Revision ID: 0016_registration_invites
Revises: 0015_auto_pair_excl_ids
Create Date: 2026-09-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
# NOTE: keep this at or under 32 chars — see the note in
# 0002_conflict_resolution.py (alembic_version is a VARCHAR(32)).
revision: str = "0016_registration_invites"
down_revision: Union[str, None] = "0015_auto_pair_excl_ids"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "invites",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        # sha256 hex of the code. The code itself exists in plaintext for exactly
        # one response (the admin's create call) and one request (the register
        # call that spends it), and is never written down — not here, and not in
        # the audit rows, which name an invite by id.
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        # Non-NULL is what "spent" means, and the claim's `WHERE used_at IS NULL`
        # is what makes spending it impossible to race.
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("used_by_user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["used_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Unique because the hash is the invite's identity, and indexed because the
    # register path looks one up by nothing else — on an unauthenticated route,
    # so the lookup must not degrade into a scan.
    op.create_index(op.f("ix_invites_code_hash"), "invites", ["code_hash"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_invites_code_hash"), table_name="invites")
    op.drop_table("invites")
