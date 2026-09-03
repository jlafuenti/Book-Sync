"""cascade a bookmark's log rows when the bookmark goes (issue #146)

Self-service account deletion (``DELETE /api/auth/me``) removes the user, and
migration 0011 made the database cascade that into ``bookmarks``. One table
further down the chain was still unguarded: ``bookmark_logs.bookmark_id``
referenced ``bookmarks.id`` with no ``ON DELETE`` action, so a cascade — or any
``DELETE`` issued in SQL — hits a ``ForeignKeyViolation`` on Postgres for
anybody who has ever moved their position. That is exactly issue #198's failure,
one level lower.

``Bookmark.logs`` already carries an ORM ``delete-orphan`` cascade, which is why
the endpoint works when the session performs the delete and why nothing noticed:
the ORM loads the children and deletes them itself. The database has to agree,
for the rows the session never loaded and for a delete written in SQL.

``position_hints.bookmark_id`` already had ``ON DELETE CASCADE`` (migration
0007) and is left alone.

Alembic autogenerate does not diff ``ondelete``, so this is hand-written and
``tests/test_schema_contract.py`` pins the result by reflection — the same
arrangement 0011 set up, for the same reason.

Only Postgres runs these migrations in this repo (``alembic/env.py`` derives a
psycopg2 URL; the SQLite test suite builds its schema with ``create_all``), but
the SQLite branch is real: batch mode copies the table, and the baseline's
unnamed constraint is addressed through Alembic's ``naming_convention`` recipe.

Revision ID: 0013_bookmark_log_cascade
Revises: 0012_book_path_indexes
Create Date: 2026-09-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
# NOTE: keep this at or under 32 chars — see the note in
# 0002_conflict_resolution.py (alembic_version is a VARCHAR(32)).
revision: str = "0013_bookmark_log_cascade"
down_revision: Union[str, None] = "0012_book_path_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = "bookmark_logs"
_FK_NAME = "fk_bookmark_logs_bookmark_id_bookmarks"

# Lets batch mode on SQLite name the baseline's unnamed FK so it can be dropped.
# Matches the name created below.
_NAMING_CONVENTION = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}


def _existing_fk_names() -> list:
    """Reflected names of ``bookmark_logs.bookmark_id -> bookmarks.id``.

    A name of ``None`` (SQLite's unnamed FKs) is preserved in the list — batch
    mode addresses those through the naming convention instead.
    """
    inspector = sa.inspect(op.get_bind())
    return [
        fk["name"]
        for fk in inspector.get_foreign_keys(_TABLE)
        if fk["constrained_columns"] == ["bookmark_id"]
        and fk["referred_table"] == "bookmarks"
    ]


def _set_fk(ondelete) -> None:
    """Recreate the FK with the given ``ON DELETE`` action."""
    existing = _existing_fk_names()

    if op.get_bind().dialect.name == "sqlite":
        # SQLite cannot ALTER a constraint at all; batch mode rebuilds the table.
        with op.batch_alter_table(_TABLE, naming_convention=_NAMING_CONVENTION) as batch:
            for name in existing:
                batch.drop_constraint(name or _FK_NAME, type_="foreignkey")
            batch.create_foreign_key(
                _FK_NAME, "bookmarks", ["bookmark_id"], ["id"], ondelete=ondelete
            )
        return

    for name in existing:
        op.drop_constraint(name, _TABLE, type_="foreignkey")
    op.create_foreign_key(
        _FK_NAME, _TABLE, "bookmarks", ["bookmark_id"], ["id"], ondelete=ondelete
    )


def upgrade() -> None:
    _set_fk("CASCADE")


def downgrade() -> None:
    # Back to no ON DELETE action — the state in which deleting an account that
    # has ever moved its position fails on Postgres. A revert, not a fix.
    _set_fk(None)
