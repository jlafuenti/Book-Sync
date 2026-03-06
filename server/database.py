"""
BookSync Database Setup

Async SQLAlchemy engine and session management for PostgreSQL.
"""

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from config import settings

# Create async engine
engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=10,
    max_overflow=20,
)

# Session factory
async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""
    pass


async def get_db() -> AsyncSession:
    """FastAPI dependency that yields a database session."""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    """Create all database tables on startup."""
    async with engine.begin() as conn:
        from models import user, book, sync_map, bookmark
        from models.settings import SystemSetting # Explicit import
        
        await conn.run_sync(Base.metadata.create_all)

        # Manual migration for series fields (since we don't have Alembic setup yet)
        # Use IF NOT EXISTS to avoid errors that abort the transaction
        # (which would roll back table creation from create_all above)
        from sqlalchemy import text
        
        # Ebooks migration
        await conn.execute(text("ALTER TABLE ebooks ADD COLUMN IF NOT EXISTS series VARCHAR(500)"))
        await conn.execute(text("ALTER TABLE ebooks ADD COLUMN IF NOT EXISTS series_index FLOAT"))
        await conn.execute(text("ALTER TABLE ebooks ADD COLUMN IF NOT EXISTS metadata_source VARCHAR(50)"))
        await conn.execute(text("ALTER TABLE ebooks ADD COLUMN IF NOT EXISTS metadata_pattern VARCHAR(500)"))

        # Audiobooks migration
        await conn.execute(text("ALTER TABLE audiobooks ADD COLUMN IF NOT EXISTS series VARCHAR(500)"))
        await conn.execute(text("ALTER TABLE audiobooks ADD COLUMN IF NOT EXISTS series_index FLOAT"))
        await conn.execute(text("ALTER TABLE audiobooks ADD COLUMN IF NOT EXISTS metadata_source VARCHAR(50)"))
        await conn.execute(text("ALTER TABLE audiobooks ADD COLUMN IF NOT EXISTS metadata_pattern VARCHAR(500)"))

        # Sync points migration
        await conn.execute(text("ALTER TABLE sync_points ADD COLUMN IF NOT EXISTS audio_text TEXT"))

        # Extended metadata migration (W7)
        for table in ("ebooks", "audiobooks"):
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS description TEXT"))
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS publisher VARCHAR(500)"))
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS publish_year INTEGER"))
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS language VARCHAR(50)"))
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS genres VARCHAR(1000)"))
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS tags VARCHAR(1000)"))
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS isbn VARCHAR(100)"))
            await conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN isbn TYPE VARCHAR(100)"))
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS asin VARCHAR(100)"))
            await conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN asin TYPE VARCHAR(100)"))
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS narrators VARCHAR(500)"))
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS is_explicit BOOLEAN"))
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS is_abridged BOOLEAN"))
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS cover_path VARCHAR(2000)"))
