"""
User model for multi-user authentication with role-based access control.
"""

from datetime import datetime
from sqlalchemy import String, DateTime, Boolean, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base
from utils import utcnow

# Role hierarchy: superadmin > admin > editor > user
ROLE_HIERARCHY = {"superadmin": 4, "admin": 3, "editor": 2, "user": 1}
VALID_ROLES = set(ROLE_HIERARCHY.keys())


class User(Base):
    """A registered user of the BookSync system."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="user", nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)  # Legacy, kept for migration
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    theme: Mapped[str] = mapped_column(String(50), nullable=False, default="blueprint")
    must_reset_password: Mapped[bool] = mapped_column(Boolean, default=False)
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )

    # Relationships
    bookmarks = relationship("Bookmark", back_populates="user", cascade="all, delete-orphan")

    def has_role(self, minimum_role: str) -> bool:
        """Check if user's role meets or exceeds the minimum required role."""
        return ROLE_HIERARCHY.get(self.role, 0) >= ROLE_HIERARCHY.get(minimum_role, 0)

    def __repr__(self) -> str:
        return f"<User(id={self.id}, username='{self.username}', role='{self.role}')>"
