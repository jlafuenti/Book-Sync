"""
Audit-log retention (issue #261).

`audit_logs` is the most personal table the server keeps — a user id, a client
IP and free text that on a failed login embeds whatever username was typed —
and it lands in every nightly dump. Nothing ever deleted from it, so a privacy
statement could not name a retention period the code actually honoured.

The window is DB-backed (`SystemSetting["audit_log_retention_days"]`, default
90) so an operator can change it from System → Settings without a redeploy.
**0 means keep forever**, which is why the cutoff is never computed for a
non-positive value: a naive `now - 0 days` would delete the entire table.

The prune itself rides the nightly backup tick (`services/backup_service._tick`)
rather than running its own loop — there is only one nightly job here, and a
second scheduler for one DELETE would be more machinery than the work deserves.
"""

import logging
from datetime import timedelta

from sqlalchemy import delete, select

from models.audit_log import AuditLog
from models.settings import SystemSetting
from utils import utcnow

logger = logging.getLogger("audit-retention")

SETTING_KEY = "audit_log_retention_days"

# Mirrors the key in routers/settings.py DEFAULT_SETTINGS.
DEFAULT_RETENTION_DAYS = 90


async def get_retention_days(db) -> int:
    """The configured retention window in days, or the default.

    Anything unparseable falls back to the default rather than raising — a
    hand-edited settings row should not stop the nightly job.
    """
    row = (await db.execute(
        select(SystemSetting).where(SystemSetting.key == SETTING_KEY)
    )).scalar_one_or_none()
    if row is None or row.value is None:
        return DEFAULT_RETENTION_DAYS
    try:
        return int(row.value)
    except (TypeError, ValueError):
        logger.warning(
            f"[audit] ignoring unparseable {SETTING_KEY}={row.value!r}, "
            f"using {DEFAULT_RETENTION_DAYS}"
        )
        return DEFAULT_RETENTION_DAYS


async def prune(db, retention_days: int) -> int:
    """Delete audit rows older than `retention_days`. Returns the row count.

    A non-positive window means "keep forever" and deletes nothing.
    """
    if retention_days <= 0:
        return 0

    cutoff = utcnow() - timedelta(days=retention_days)
    result = await db.execute(delete(AuditLog).where(AuditLog.created_at < cutoff))
    await db.commit()
    return result.rowcount or 0


async def run(db) -> int:
    """Read the configured window and prune. Returns rows deleted."""
    return await prune(db, await get_retention_days(db))
