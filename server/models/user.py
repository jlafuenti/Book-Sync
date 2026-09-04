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

# Score for a role nobody recognises. An unknown *user* role scoring 0 is the
# safe direction — it clears nothing. An unknown *minimum* scoring 0 is the
# dangerous one, because every caller clears a bar of 0, so a typo in a required
# role does not fail the check, it deletes it (issue #359). Minimums are
# therefore never scored: they are rejected outright, here and in
# `routers.auth.require_role`.
_UNKNOWN_ROLE_LEVEL = 0


class User(Base):
    """A registered user of the Tandem system."""

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
    #
    # Both cascades matter: the ORM one deletes the children in the same flush
    # as the user (so `DELETE /api/users/{id}` works), and the matching
    # `ondelete="CASCADE"` on the FK itself (migration 0011) means the database
    # would do it anyway — for a DELETE issued in SQL, or a row the session
    # never loaded. `user_progress` had neither, so deleting anyone who had
    # opened a book raised a ForeignKeyViolation on Postgres and the admin got
    # a bare 500 (issue #198).
    #
    # `audit_logs` is deliberately not cascaded: its `user_id` is SET NULL, so
    # the record of what an account did outlives the account.
    bookmarks = relationship("Bookmark", back_populates="user", cascade="all, delete-orphan")
    progress = relationship("UserProgress", back_populates="user",
                            cascade="all, delete-orphan")

    def has_role(self, minimum_role: str) -> bool:
        """True if this user's role meets or exceeds `minimum_role`.

        Fails closed on a minimum that is not a real role (issue #359): a bar
        nobody can name is cleared by nobody, not by everybody. Callers that
        want the typo to be loud rather than silent should use
        `routers.auth.require_role`, which raises.
        """
        if minimum_role not in ROLE_HIERARCHY:
            return False
        return ROLE_HIERARCHY.get(self.role, _UNKNOWN_ROLE_LEVEL) >= ROLE_HIERARCHY[minimum_role]

    def __repr__(self) -> str:
        return f"<User(id={self.id}, username='{self.username}', role='{self.role}')>"
