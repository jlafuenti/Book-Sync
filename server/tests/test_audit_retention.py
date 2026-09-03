"""
Audit-log retention tests (issue #261).

`audit_logs` is the most personal table in every nightly dump — user ids, client
IPs and free text that on a failed login embeds whatever username was typed. It
grew forever. These cover the pruner itself (cutoff, idempotence, the
keep-forever escape hatch), the DB-backed setting it reads, and the fact that
the nightly backup tick actually runs it.
"""

from datetime import timedelta

from sqlalchemy import select

from config import settings
from models.audit_log import AuditLog
from models.settings import SystemSetting
from routers import settings as settings_router
from services import audit_retention
from services import backup_service as bs
from tests.factories import ensure_users
from utils import utcnow


async def _seed_ages(db, *ages_in_days):
    """Insert one audit row per age (in days before now). Returns their ids."""
    await ensure_users(db, 1)
    now = utcnow()
    ids = []
    for age in ages_in_days:
        row = AuditLog(
            user_id=1,
            action="login_failed",
            details=f"row aged {age}d",
            ip_address="10.0.0.1",
            created_at=now - timedelta(days=age),
        )
        db.add(row)
        await db.flush()
        ids.append(row.id)
    await db.commit()
    return ids


async def _remaining(db):
    rows = (await db.execute(select(AuditLog.details))).scalars().all()
    return sorted(rows)


# ── prune ──────────────────────────────────────────────────────────────────

async def test_prune_deletes_only_rows_older_than_retention(db):
    await _seed_ages(db, 100, 89, 1)

    deleted = await audit_retention.prune(db, 90)

    assert deleted == 1
    assert await _remaining(db) == ["row aged 1d", "row aged 89d"]


async def test_prune_is_idempotent(db):
    await _seed_ages(db, 100, 89, 1)

    assert await audit_retention.prune(db, 90) == 1
    # Second pass has nothing left to do — no rows, no error.
    assert await audit_retention.prune(db, 90) == 0
    assert len(await _remaining(db)) == 2


async def test_prune_zero_keeps_everything(db):
    """0 is the documented "keep forever" value — it must never mean "delete
    everything older than now", which is what a naive cutoff would do."""
    await _seed_ages(db, 100, 89, 1)

    assert await audit_retention.prune(db, 0) == 0
    assert len(await _remaining(db)) == 3


async def test_prune_negative_keeps_everything(db):
    """A hand-edited setting row can hold -1; treat it like 0 rather than
    computing a cutoff in the future and wiping the table."""
    await _seed_ages(db, 100, 1)

    assert await audit_retention.prune(db, -1) == 0
    assert len(await _remaining(db)) == 2


# ── the setting ────────────────────────────────────────────────────────────

async def test_default_retention_is_90_days(db):
    assert audit_retention.DEFAULT_RETENTION_DAYS == 90
    assert settings_router.DEFAULT_SETTINGS["audit_log_retention_days"] == 90
    # Nothing stored yet -> the default.
    assert await audit_retention.get_retention_days(db) == 90


async def test_get_retention_days_reads_the_setting(db):
    db.add(SystemSetting(key="audit_log_retention_days", value="30"))
    await db.commit()

    assert await audit_retention.get_retention_days(db) == 30


async def test_get_retention_days_falls_back_on_garbage(db):
    db.add(SystemSetting(key="audit_log_retention_days", value="not-a-number"))
    await db.commit()

    assert await audit_retention.get_retention_days(db) == 90


async def test_setting_round_trips_through_the_settings_api(make_client, make_user, auth_header, db):
    admin = await make_user(username="admin-audit", role="admin")
    async with make_client(settings_router.router) as c:
        before = await c.get("/api/settings/", headers=auth_header(admin))
        assert before.json()["audit_log_retention_days"] == 90

        put = await c.put(
            "/api/settings/", headers=auth_header(admin),
            json={"audit_log_retention_days": 30},
        )
        assert put.status_code == 200
        # Typed back as an int, not the string it is stored as.
        assert put.json()["audit_log_retention_days"] == 30

        get = await c.get("/api/settings/", headers=auth_header(admin))
        assert get.json()["audit_log_retention_days"] == 30

    # ...and the value the API wrote is the one the pruner reads.
    assert await audit_retention.get_retention_days(db) == 30


# ── the nightly tick ───────────────────────────────────────────────────────

async def test_tick_prunes_audit_logs(monkeypatch, tmp_path, db):
    """The backup scheduler's tick is the only nightly loop; the pruner rides
    it. Backup hour is deliberately off so nothing tries to shell out to
    pg_dump — the prune must happen regardless of whether a backup is due."""
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    (tmp_path / "booksync-db-2020-01-01.dump").write_bytes(b"x")  # not a fresh deploy
    db.add(SystemSetting(key="backup_hour", value=str((utcnow().hour + 1) % 24)))
    await db.commit()
    await _seed_ages(db, 100, 1)

    await bs._tick()

    assert await _remaining(db) == ["row aged 1d"]


async def test_tick_honours_a_zero_retention_setting(monkeypatch, tmp_path, db):
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    (tmp_path / "booksync-db-2020-01-01.dump").write_bytes(b"x")
    db.add(SystemSetting(key="backup_hour", value=str((utcnow().hour + 1) % 24)))
    db.add(SystemSetting(key="audit_log_retention_days", value="0"))
    await db.commit()
    await _seed_ages(db, 5000, 1)

    await bs._tick()

    assert len(await _remaining(db)) == 2


async def test_tick_survives_a_failing_prune(monkeypatch, tmp_path, db):
    """A broken prune must not take the nightly backup down with it."""
    monkeypatch.setattr(settings, "backups_dir", str(tmp_path))
    (tmp_path / "booksync-db-2020-01-01.dump").write_bytes(b"x")
    db.add(SystemSetting(key="backup_hour", value=str((utcnow().hour + 1) % 24)))
    await db.commit()

    async def boom(_db):
        raise RuntimeError("prune exploded")

    monkeypatch.setattr(audit_retention, "run", boom)

    await bs._tick()  # must not raise
