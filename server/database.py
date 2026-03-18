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
        from models.transcription_queue import TranscriptionQueueItem  # Queue model
        from models.transcript import AudioTranscript  # Transcript cache model
        from models.audit_log import AuditLog  # Audit log model

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

        # Transcription Queue migration
        await conn.execute(text("ALTER TABLE transcription_queue ADD COLUMN IF NOT EXISTS retry_count INTEGER DEFAULT 0"))

        # Book pairs migration
        await conn.execute(text("ALTER TABLE book_pairs ADD COLUMN IF NOT EXISTS ignored_fields JSONB NOT NULL DEFAULT '[]'::jsonb"))

        # User theme preference migration
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS theme VARCHAR(50) NOT NULL DEFAULT 'blueprint'"))

        # RBAC migration
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(20) DEFAULT 'user'"))
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS must_reset_password BOOLEAN DEFAULT false"))
        # Migrate existing is_admin=true users to role='admin' (one-time)
        await conn.execute(text("UPDATE users SET role = 'admin' WHERE is_admin = true AND role = 'user'"))


async def bootstrap_superadmin():
    """Create default superadmin account if no users exist, or promote first admin."""
    import logging
    from sqlalchemy import select, func
    from models.user import User

    logger = logging.getLogger(__name__)

    async with async_session() as session:
        # Count total users
        result = await session.execute(select(func.count(User.id)))
        user_count = result.scalar()

        if user_count == 0:
            # Fresh install — create default superadmin
            from routers.auth import hash_password
            user = User(
                username="admin",
                email="admin@localhost",
                hashed_password=hash_password("admin"),
                role="superadmin",
                is_admin=True,
                is_active=True,
                must_reset_password=True,
            )
            session.add(user)
            await session.commit()
            logger.info("Created default superadmin account (admin/admin). Password reset required on first login.")
        else:
            # Existing install — ensure at least one superadmin exists
            result = await session.execute(
                select(User).where(User.role == "superadmin")
            )
            if result.scalar_one_or_none() is None:
                # Promote the first admin user to superadmin
                result = await session.execute(
                    select(User).where(User.is_admin == True).order_by(User.id).limit(1)
                )
                admin_user = result.scalar_one_or_none()
                if admin_user:
                    admin_user.role = "superadmin"
                    await session.commit()
                    logger.info(f"Promoted user '{admin_user.username}' to superadmin role.")
                else:
                    # No admins at all — promote the first user
                    result = await session.execute(
                        select(User).order_by(User.id).limit(1)
                    )
                    first_user = result.scalar_one_or_none()
                    if first_user:
                        first_user.role = "superadmin"
                        first_user.is_admin = True
                        await session.commit()
                        logger.info(f"Promoted user '{first_user.username}' to superadmin (no admins found).")
