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

        # Audiobooks migration
        await conn.execute(text("ALTER TABLE audiobooks ADD COLUMN IF NOT EXISTS series VARCHAR(500)"))
        await conn.execute(text("ALTER TABLE audiobooks ADD COLUMN IF NOT EXISTS series_index FLOAT"))

