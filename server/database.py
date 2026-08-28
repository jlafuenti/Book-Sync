"""
Tandem Database Setup

Async SQLAlchemy engine and session management for PostgreSQL.
"""

import logging

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from config import settings

# The randomly generated bootstrap password is the one secret this process ever
# prints. README tells the operator to read it from `docker compose logs`, so it
# has to reach stdout — but main.py attaches a RotatingFileHandler (10 MB x 5) to
# the root logger, and a plaintext superadmin password sitting in
# <APP_DATA_DIR>/logs/server.log across five rotations is a second, much
# longer-lived copy that nothing asked for (issue #209).
#
# A dedicated logger with propagate=False and its own StreamHandler reaches the
# container log and stops there.
BOOTSTRAP_LOGGER_NAME = "booksync.bootstrap"


def bootstrap_logger() -> logging.Logger:
    log = logging.getLogger(BOOTSTRAP_LOGGER_NAME)
    log.propagate = False
    log.setLevel(logging.INFO)
    if not any(isinstance(h, logging.StreamHandler) for h in log.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        log.addHandler(handler)
    return log

# Create async engine. pool_size/max_overflow are QueuePool options that the
# SQLite (aiosqlite) dialect rejects — it uses NullPool — so only pass them for
# the real Postgres engine. This lets the test suite point DATABASE_URL at a
# throwaway SQLite DB without tripping over invalid kwargs.
_engine_kwargs: dict = {"echo": False}
if not settings.database_url.startswith("sqlite"):
    _engine_kwargs.update(pool_size=10, max_overflow=20)

engine = create_async_engine(settings.database_url, **_engine_kwargs)

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
            # Fresh install — create superadmin with a randomly generated
            # password (never "admin"/"admin"). The operator reads it from
            # server/container logs and must change it on first login.
            import secrets
            from routers.auth import hash_password
            generated_password = secrets.token_urlsafe(16)
            user = User(
                username="admin",
                email="admin@localhost",
                hashed_password=hash_password(generated_password),
                role="superadmin",
                is_admin=True,
                is_active=True,
                must_reset_password=True,
            )
            session.add(user)
            await session.commit()
            bootstrap_logger().warning(
                "Created default superadmin account 'admin' with a randomly generated "
                "password: %s — change it via first login (password reset is required).",
                generated_password,
            )
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
