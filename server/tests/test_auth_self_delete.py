"""
Self-service account deletion — ``DELETE /api/auth/me`` (issue #146).

Google Play requires any app that can create an account to offer an in-app way
to delete it. Until this endpoint existed the only delete was
``DELETE /api/users/{id}``, which is admin-gated and explicitly refuses the
caller's own account.

Three things are pinned here beyond "the row is gone":

* **Every dependent row goes with it.** Bookmarks, their logs, their position
  hints and the progress rows. The SQLite harness enforces foreign keys
  (``conftest.py``), so a missing cascade fails here the same way it 500s on
  Postgres — which is exactly how issue #198 escaped.
* **The audit trail survives, redacted.** ``audit_logs.user_id`` is
  ``ON DELETE SET NULL``, so the row that records the deletion outlives the
  account with a nulled user id. ``target_user_id`` carries no FK and keeps the
  number, which is what makes the row useful to an operator afterwards.
* **The last active superadmin is refused (409).** A self-hosted deployment
  with no superadmin left has no way to approve a registration, promote anyone
  or reach the admin console — an unrecoverable state reached by one tap.
"""

import pytest
from sqlalchemy import func, select

from models.audit_log import AuditLog
from models.bookmark import Bookmark, BookmarkLog, BookmarkSource, HintKind, PositionHint
from models.progress import ProgressType, UserProgress
from models.user import User
from tests.factories import make_book_pair


@pytest.fixture(autouse=True)
def _reset_password_failure_tracker():
    """The endpoint shares ``failed_password_changes`` with change-password.

    Its state is process-global and conftest only resets the *login* tracker, so
    without this a run of wrong-password tests would leak a lockout into the
    next test in this file.
    """
    from rate_limit import failed_password_changes

    failed_password_changes.reset()
    yield
    failed_password_changes.reset()


async def seed_positions(db, user, pair):
    """Give `user` one bookmark (with a log row and a hint) and one progress row.

    Returns the bookmark id — every child row is reachable from it, and the
    point of the test is that none of them are reachable afterwards.
    """
    bookmark = Bookmark(
        user_id=user.id,
        book_pair_id=pair.id,
        source=BookmarkSource.EBOOK,
        epub_chapter=1,
        epub_sentence_index=4,
        anchor_revision=1,
    )
    db.add(bookmark)
    await db.flush()

    db.add(BookmarkLog(
        bookmark_id=bookmark.id,
        source=BookmarkSource.EBOOK,
        new_epub_chapter=1,
        new_epub_sentence_index=4,
    ))
    db.add(PositionHint(
        bookmark_id=bookmark.id,
        device_id="test-device",
        hint_kind=HintKind.EPUBJS_CFI,
        hint_value="epubcfi(/6/4!/4/2/2[p1]/1:0)",
        anchor_revision=1,
    ))
    db.add(UserProgress(
        user_id=user.id,
        book_pair_id=pair.id,
        media_type=ProgressType.EBOOK,
        epub_chapter=1,
        epub_progress_percent=12.5,
    ))
    await db.commit()
    return bookmark.id


async def count(db, model, **filters):
    stmt = select(func.count()).select_from(model)
    for column, value in filters.items():
        stmt = stmt.where(getattr(model, column) == value)
    return (await db.execute(stmt)).scalar_one()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

async def test_self_delete_removes_the_user(client, make_user, auth_header, db):
    user = await make_user(username="leaver", password="s3cret")
    r = await client.request(
        "DELETE", "/api/auth/me",
        headers=auth_header(user), json={"password": "s3cret"},
    )
    assert r.status_code == 204, r.text
    assert r.content == b""
    assert await count(db, User, id=user.id) == 0


async def test_self_delete_removes_every_dependent_row(client, make_user, auth_header, db):
    """Bookmarks, bookmark logs, position hints and progress all go.

    Foreign keys are enforced in this harness, so a row the cascade misses
    either survives as an orphan (caught below) or makes the DELETE itself fail.
    """
    user = await make_user(username="reader", password="pw12345")
    pair = await make_book_pair(db)
    bookmark_id = await seed_positions(db, user, pair)

    # Precondition: the rows this test is about actually exist.
    assert await count(db, Bookmark, user_id=user.id) == 1
    assert await count(db, BookmarkLog, bookmark_id=bookmark_id) == 1
    assert await count(db, PositionHint, bookmark_id=bookmark_id) == 1
    assert await count(db, UserProgress, user_id=user.id) == 1

    r = await client.request(
        "DELETE", "/api/auth/me",
        headers=auth_header(user), json={"password": "pw12345"},
    )
    assert r.status_code == 204, r.text

    assert await count(db, Bookmark, user_id=user.id) == 0
    assert await count(db, BookmarkLog, bookmark_id=bookmark_id) == 0
    assert await count(db, PositionHint, bookmark_id=bookmark_id) == 0
    assert await count(db, UserProgress, user_id=user.id) == 0


async def test_self_delete_leaves_a_redacted_audit_row(client, make_user, auth_header, db):
    """The record of the deletion outlives the account, with no user id on it."""
    user = await make_user(username="gone", password="pw12345")

    r = await client.request(
        "DELETE", "/api/auth/me",
        headers=auth_header(user), json={"password": "pw12345"},
    )
    assert r.status_code == 204, r.text

    rows = (await db.execute(
        select(AuditLog).where(AuditLog.action == "account_self_deleted")
    )).scalars().all()
    assert len(rows) == 1, "expected exactly one audit row for the deletion"
    entry = rows[0]
    # SET NULL redacts the actor: there is no user to point at any more.
    assert entry.user_id is None
    # target_user_id carries no FK, so the id stays readable to an operator.
    assert entry.target_user_id == user.id


async def test_access_token_stops_working_after_self_delete(client, make_user, auth_header):
    user = await make_user(username="ghosted", password="pw12345")
    header = auth_header(user)

    assert (await client.get("/api/auth/me", headers=header)).status_code == 200

    r = await client.request("DELETE", "/api/auth/me",
                             headers=header, json={"password": "pw12345"})
    assert r.status_code == 204, r.text

    assert (await client.get("/api/auth/me", headers=header)).status_code == 401


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------

async def test_wrong_password_is_refused_and_deletes_nothing(
    client, make_user, auth_header, db,
):
    user = await make_user(username="fumble", password="right-one")
    pair = await make_book_pair(db)
    await seed_positions(db, user, pair)

    r = await client.request(
        "DELETE", "/api/auth/me",
        headers=auth_header(user), json={"password": "wrong-one"},
    )
    assert r.status_code == 403
    assert "password" in r.json()["detail"].lower()

    assert await count(db, User, id=user.id) == 1
    assert await count(db, Bookmark, user_id=user.id) == 1
    assert await count(db, UserProgress, user_id=user.id) == 1


async def test_wrong_password_is_audited(client, make_user, auth_header, db):
    user = await make_user(username="fumble2", password="right-one")
    await client.request("DELETE", "/api/auth/me",
                         headers=auth_header(user), json={"password": "nope"})

    assert await count(db, AuditLog, action="account_delete_failed") == 1


async def test_last_active_superadmin_is_refused(client, make_user, auth_header, db):
    """409, not 403: the request is well-formed and authorised — the *state* of
    the deployment is what forbids it, and the message has to say so."""
    root = await make_user(username="root", password="pw12345", role="superadmin")

    r = await client.request(
        "DELETE", "/api/auth/me",
        headers=auth_header(root), json={"password": "pw12345"},
    )
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "superadmin" in detail.lower()

    assert await count(db, User, id=root.id) == 1


async def test_inactive_superadmin_does_not_count_as_a_replacement(
    client, make_user, auth_header, db,
):
    """A deactivated superadmin cannot sign in, so it cannot be the one left."""
    root = await make_user(username="root2", password="pw12345", role="superadmin")
    await make_user(username="retired", password="pw12345",
                    role="superadmin", is_active=False)

    r = await client.request(
        "DELETE", "/api/auth/me",
        headers=auth_header(root), json={"password": "pw12345"},
    )
    assert r.status_code == 409
    assert await count(db, User, id=root.id) == 1


async def test_superadmin_may_leave_when_another_active_one_remains(
    client, make_user, auth_header, db,
):
    root = await make_user(username="root3", password="pw12345", role="superadmin")
    await make_user(username="root4", password="pw12345", role="superadmin")

    r = await client.request(
        "DELETE", "/api/auth/me",
        headers=auth_header(root), json={"password": "pw12345"},
    )
    assert r.status_code == 204, r.text
    assert await count(db, User, id=root.id) == 0


async def test_a_plain_admin_is_never_the_last_superadmin(
    client, make_user, auth_header, db,
):
    """The guard is about superadmins only — an admin leaving is unremarkable."""
    await make_user(username="root5", password="pw12345", role="superadmin")
    admin = await make_user(username="anadmin", password="pw12345", role="admin")

    r = await client.request(
        "DELETE", "/api/auth/me",
        headers=auth_header(admin), json={"password": "pw12345"},
    )
    assert r.status_code == 204, r.text
    assert await count(db, User, id=admin.id) == 0


async def test_self_delete_requires_authentication(client):
    r = await client.request("DELETE", "/api/auth/me", json={"password": "x"})
    assert r.status_code == 401


async def test_repeated_wrong_passwords_are_locked_out(client, make_user, auth_header):
    """Same bcrypt oracle as change-password (#264), so the same bucket.

    Without a bound, a stolen access token can brute-force the account password
    against this endpoint instead — and the payoff here is destruction, not just
    a takeover.
    """
    from config import settings

    user = await make_user(username="hammer", password="right-one")
    header = auth_header(user)

    for _ in range(settings.password_change_failure_limit):
        r = await client.request("DELETE", "/api/auth/me",
                                 headers=header, json={"password": "wrong"})
        assert r.status_code == 403

    # Locked: even the correct password gets 429 now, so the lockout can never
    # be used as a password oracle either.
    r = await client.request("DELETE", "/api/auth/me",
                             headers=header, json={"password": "right-one"})
    assert r.status_code == 429
    assert r.headers.get("Retry-After")
