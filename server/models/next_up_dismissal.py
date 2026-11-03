"""
Per-account "Next up" dismissals (issue #921).

The Next up rule itself stays client-side; the server only remembers which
series a user removed so the phone and the web agree.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class NextUpDismissal(Base):
    __tablename__ = "next_up_dismissals"
    __table_args__ = (
        Index("ux_next_up_dismissals_user_series", "user_id", "series_key", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # The series name trimmed and case-folded: what the clients match on.
    series_key: Mapped[str] = mapped_column(String(500), nullable=False)
    # The name as sent (stripped), for display.
    series: Mapped[str] = mapped_column(String(500), nullable=False)
    # Naive UTC, like every other timestamp column (`utils.utcnow`).
    dismissed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
