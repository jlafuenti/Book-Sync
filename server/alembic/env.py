"""Alembic environment for Tandem.

The application runs on async drivers (asyncpg / aiosqlite); Alembic needs a
sync driver, so ``_sync_url()`` rewrites the driver in ``settings.database_url``
(asyncpg -> psycopg2, aiosqlite -> pysqlite). Everything else — credentials,
host, database name — is inherited from the same settings the app uses, so
migrations and the app can never drift onto different databases.

``target_metadata`` is the ORM's ``Base.metadata`` with every model module
imported, which is what ``alembic revision --autogenerate`` and ``alembic check``
(the CI drift gate) compare against.
"""

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# env.py lives in server/alembic/; make the server package importable.
_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)

from config import settings  # noqa: E402
from database import Base  # noqa: E402

# Import every model module so Base.metadata is complete. Mirrors the import
# list in tests/conftest.py — keep them in sync when adding a model.
from models import user, book, sync_map, bookmark, progress  # noqa: E402,F401
from models.settings import SystemSetting  # noqa: E402,F401
from models.transcription_queue import TranscriptionQueueItem  # noqa: E402,F401
from models.transcript import AudioTranscript  # noqa: E402,F401
from models.audit_log import AuditLog  # noqa: E402,F401
from models.refresh_token import RefreshToken  # noqa: E402,F401
from models.import_source import (  # noqa: E402,F401
    ImportSource,
    ImportSourceCredential,
    ImportJob,
)
from models.library_issue import LibraryCheckResult  # noqa: E402,F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _sync_url() -> str:
    """Rewrite the app's async DB URL to the sync driver Alembic requires."""
    return (
        settings.database_url
        .replace("+asyncpg", "+psycopg2")
        .replace("+aiosqlite", "+pysqlite")
    )


def run_migrations_offline() -> None:
    context.configure(
        url=_sync_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _sync_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
