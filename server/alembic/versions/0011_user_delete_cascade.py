"""cascade a user's positions when the account is deleted (issue #198)

``DELETE /api/users/{id}`` worked only for users who had never opened a book.
``user_progress.user_id`` and ``bookmarks.user_id`` referenced ``users.id`` with
no ``ON DELETE`` action, so Postgres raised ``ForeignKeyViolation`` for anyone
with a reading position, ``get_db`` rolled the request back, and the admin got
a bare 500. Removing an account is exactly what an internet-facing deployment
has to be able to do.

``User.bookmarks`` already carried an ORM ``delete-orphan`` cascade and
``User.progress`` now does too, which is what makes the endpoint work. This
migration makes the *database* agree: the ORM cascade only fires for a delete
the session performs, and a row the session never loaded — or a ``DELETE``
issued in SQL — must not be able to leave the account undeletable or the row
orphaned. Alembic autogenerate does not diff ``ondelete``, so this is
hand-written and ``tests/test_schema_contract.py`` pins the result by
reflection, or the two halves drift apart again unnoticed.

``audit_logs.user_id`` is deliberately left at ``SET NULL`` (set in the
baseline): the record of what an account did must outlive the account.

The pre-existing constraints are unnamed in the baseline, so Postgres named
them ``<table>_user_id_fkey``; the name is looked up by reflection rather than
assumed, because a database that predates Alembic and was ``stamp``ed may carry
a different one. The replacements get explicit names.

Only Postgres runs these migrations in this repo (``alembic/env.py`` derives a
psycopg2 URL; the SQLite test suite builds its schema with ``create_all``), but
the SQLite branch is real — batch mode copies the table, and unnamed reflected
constraints are addressed through Alembic's documented ``naming_convention``
recipe.

Revision ID: 0011_user_delete_cascade
Revises: 0010_syncmap_epub_hash
Create Date: 2026-09-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
# NOTE: keep this at or under 32 chars — see the note in
# 0002_conflict_resolution.py (alembic_version is a VARCHAR(32)).
revision: str = "0011_user_delete_cascade"
down_revision: Union[str, None] = "0010_syncmap_epub_hash"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Tables whose `user_id` must go when the user does.
_TABLES = ("user_progress", "bookmarks")

# Lets batch mode on SQLite name the baseline's unnamed FKs so they can be
# dropped. Matches the names created below.
_NAMING_CONVENTION = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}


def _fk_name(table: str) -> str:
    return f"fk_{table}_user_id_users"


def _existing_user_fk_names(table: str) -> list:
    """Reflected names of [table]'s `user_id -> users.id` constraints.

    A name of ``None`` (SQLite's unnamed FKs) is preserved in the list — batch
    mode addresses those through the naming convention instead.
    """
    inspector = sa.inspect(op.get_bind())
    return [
        fk["name"]
        for fk in inspector.get_foreign_keys(table)
        if fk["constrained_columns"] == ["user_id"] and fk["referred_table"] == "users"
    ]


def _set_user_fk(table: str, ondelete) -> None:
    """Recreate [table]'s `user_id` FK with the given ``ON DELETE`` action."""
    existing = _existing_user_fk_names(table)
    new_name = _fk_name(table)

    if op.get_bind().dialect.name == "sqlite":
        # SQLite cannot ALTER a constraint at all; batch mode rebuilds the
        # table. The convention supplies a name for the unnamed originals.
        with op.batch_alter_table(table, naming_convention=_NAMING_CONVENTION) as batch:
            for name in existing:
                batch.drop_constraint(name or new_name, type_="foreignkey")
            batch.create_foreign_key(
                new_name, "users", ["user_id"], ["id"], ondelete=ondelete
            )
        return

    for name in existing:
        op.drop_constraint(name, table, type_="foreignkey")
    op.create_foreign_key(new_name, table, "users", ["user_id"], ["id"], ondelete=ondelete)


def upgrade() -> None:
    for table in _TABLES:
        _set_user_fk(table, "CASCADE")


def downgrade() -> None:
    # Back to no ON DELETE action — which is the state that made the delete
    # endpoint fail, so this is a revert, not a fix.
    for table in _TABLES:
        _set_user_fk(table, None)
