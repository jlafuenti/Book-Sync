"""
Who owns the request transaction (issue #259).

`get_db` commits once, after the handler returns. A handler that also calls
`await db.commit()` is either doing something deliberate or repeating what
`get_db` is about to do anyway, and reading the handler does not tell you which
-- so the rule is written down in `docs/request-transactions.md` and the
deliberate ones carry a comment saying why.

This file pins the deliberate ones, so the opportunistic clean-up the doc
invites cannot delete one by accident. Each test says what it would catch.
"""

import pytest
from sqlalchemy import select

from models.book import AudioBook, EBook
from routers import library
from tests.factories import make_book_pair


async def _resolve(client, pair_id, user, auth_header, **updates):
    return await client.post(
        f"/api/library/pairs/{pair_id}/resolve-discrepancies",
        headers=auth_header(user),
        json={
            "ebook_updates": updates.get("ebook", {}),
            "audiobook_updates": updates.get("audiobook", {}),
        },
    )


async def test_resolved_metadata_survives_a_file_write_that_raises(
    db, make_client, make_user, auth_header, monkeypatch
):
    """`resolve_metadata_discrepancy` commits *before* it touches the files.

    `_write_ebook_metadata` rewrites the EPUB's OPF in place and is not wrapped
    in a try/except here, so it can take the handler down with it. Without the
    explicit commit, `get_db` would see the exception, roll back, and throw away
    a resolution the operator had already made -- while the file on disk may
    already be half-rewritten. Committing first means the two can only disagree
    in the harmless direction: the library knows, the file does not yet.

    Delete the `await db.commit()` at the top of that `if ebook_changed or
    audio_changed:` block and this test fails.
    """
    user = await make_user(username="tx1", role="editor")
    pair = await make_book_pair(db, ebook_title="Old title")

    def _boom(*args, **kwargs):
        raise RuntimeError("OPF rewrite blew up")

    monkeypatch.setattr(library, "_write_ebook_metadata", _boom)

    async with make_client(library.router) as c:
        with pytest.raises(RuntimeError):
            await _resolve(c, pair.id, user, auth_header,
                           ebook={"title": "Resolved title"})

    # A session of its own: the point is what reached the database, not what the
    # request's session still holds in memory.
    reread = await db.get(EBook, pair.ebook_id)
    await db.refresh(reread)
    assert reread.title == "Resolved title"


async def test_a_clean_resolution_still_lands(
    db, make_client, make_user, auth_header, monkeypatch
):
    """The ordinary path, so the test above is pinning the ordering, not the write."""
    user = await make_user(username="tx2", role="editor")
    pair = await make_book_pair(db, ebook_title="Old title")
    monkeypatch.setattr(library, "_write_ebook_metadata", lambda *a, **k: None)

    async with make_client(library.router) as c:
        resp = await _resolve(c, pair.id, user, auth_header,
                              ebook={"title": "Resolved title"})

    assert resp.status_code == 200
    reread = await db.get(EBook, pair.ebook_id)
    await db.refresh(reread)
    assert reread.title == "Resolved title"


async def test_deleting_a_book_commits_before_unlinking_the_file(
    db, make_client, make_user, auth_header, monkeypatch, tmp_path
):
    """`delete_ebook` commits the row deletion, *then* removes the file.

    The unlink is best-effort and its `OSError` is swallowed, so today the order
    is not observable from the response. It is still the order that has to hold:
    reversed, an unlink that failed in a way the handler did not catch would
    leave the row rolled back and the file gone -- a library entry pointing at
    nothing, which is exactly what `verify` exists to find.

    What this pins is the outcome either way: the row is gone and the file was
    asked for by name.
    """
    user = await make_user(username="tx3", role="editor")
    path = tmp_path / "gone.epub"
    path.write_bytes(b"x")
    eb = EBook(title="Doomed", author="A", filename="gone.epub",
               file_path=str(path), format="epub")
    db.add(eb)
    await db.commit()
    await db.refresh(eb)

    unlinked = []
    monkeypatch.setattr(library.os, "unlink", lambda p: unlinked.append(p))

    async with make_client(library.router) as c:
        resp = await c.delete(
            f"/api/library/ebooks/{eb.id}?delete_file=true",
            headers=auth_header(user),
        )

    assert resp.status_code == 204
    assert unlinked == [str(path)]
    # `db.get` would hand back this session's cached instance; ask the database.
    db.expunge_all()
    surviving = (await db.execute(
        select(EBook).where(EBook.id == eb.id)
    )).scalar_one_or_none()
    assert surviving is None


async def test_abs_enrichment_persists_when_the_tag_write_fails(
    db, make_client, make_user, auth_header, monkeypatch
):
    """The row change stands even though the file could not be tagged.

    This is the contract the response advertises -- "Metadata updated in the
    library, but writing tags to the file failed" -- and it is why the commit in
    `enrich_audiobook_from_abs` sits after the tag write rather than the handler
    raising on it.

    Honest about its teeth: `write_metadata_to_file` reports failure by
    returning, not by raising, so `get_db` would commit at teardown anyway and
    deleting that line would not turn this red. What is pinned is the outcome --
    a failed tag write must not cost the operator the metadata -- which is the
    thing a future refactor could actually break, e.g. by raising on a failed
    write instead of reporting it.
    """
    user = await make_user(username="tx4", role="editor")
    ab = AudioBook(title="Old", author="A", filename="b.m4b",
                   file_path="/x/tx4/b.m4b", format="m4b")
    db.add(ab)
    await db.commit()
    await db.refresh(ab)

    async def _fake_settings(_db):
        return True, "http://abs.invalid", "token", ""

    monkeypatch.setattr(library, "_load_abs_settings", _fake_settings)
    monkeypatch.setattr(library, "fetch_abs_index", lambda url, token, prefix: {"x": {}})
    monkeypatch.setattr(
        library, "enrich_from_abs",
        lambda meta, path, index, prefix, force=False: (
            {**meta, "description": "From ABS"}, True, True
        ),
    )
    monkeypatch.setattr(
        library, "write_metadata_to_file",
        lambda path, meta: (False, "could not open the file for writing"),
    )

    async with make_client(library.router) as c:
        resp = await c.post(
            f"/api/library/audiobooks/{ab.id}/enrich-abs",
            headers=auth_header(user),
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "tag_write_failed"

    reread = await db.get(AudioBook, ab.id)
    await db.refresh(reread)
    assert reread.description == "From ABS"
