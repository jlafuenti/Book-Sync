"""
Import-source models: per-source state, encrypted credentials, and per-run job history.
"""

from datetime import datetime
from sqlalchemy import String, Text, DateTime, Integer, Boolean, LargeBinary
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class ImportSource(Base):
    """
    Per-source configuration row. There is at most one row per source_key
    (e.g. "audible", "acsm", "kobo"). Non-secret config only — credentials
    live in ImportSourceCredential.
    """

    __tablename__ = "import_sources"

    source_key: Mapped[str] = mapped_column(String(50), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    auto_sync_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cadence_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24)

    last_sync_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[str] = mapped_column(String(20), nullable=True)  # "succeeded", "failed", "running"
    last_message: Mapped[str] = mapped_column(Text, nullable=True)

    # Live progress while a sync is running. Cleared when the job finishes.
    progress_current: Mapped[int] = mapped_column(Integer, nullable=True)
    progress_total: Mapped[int] = mapped_column(Integer, nullable=True)
    progress_title: Mapped[str] = mapped_column(String(500), nullable=True)

    # Optional source-specific JSON config blob (e.g. Kobo desktop path).
    config: Mapped[str] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class ImportSourceCredential(Base):
    """Encrypted-at-rest credential blob for one source."""

    __tablename__ = "import_source_credentials"

    source_key: Mapped[str] = mapped_column(String(50), primary_key=True)
    blob_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class ImportJob(Base):
    """One execution of a source sync — for history / debugging."""

    __tablename__ = "import_jobs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source_key: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")  # running|succeeded|failed
    trigger: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")  # manual|scheduled|upload
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    items_added: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str] = mapped_column(Text, nullable=True)
    detail: Mapped[str] = mapped_column(Text, nullable=True)  # free-form summary, e.g. list of titles added
