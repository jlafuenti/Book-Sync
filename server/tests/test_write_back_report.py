"""
The metadata PATCH reports a failed file write-back (issue #894).

The row is committed either way -- the user's edit is kept -- but the response
now says whether the file followed, instead of a silent 200.
"""

import os

import pytest

from tests.factories import make_audiobook, make_ebook, write_epub

DENIED = "[Errno 13] Permission denied: 'x.m4b'"


@pytest.fixture
async def editor_client(make_client, make_user, auth_header):
    from routers import library

    user = await make_user(username="editor", role="admin")
    async with make_client(library.router) as c:
        yield c, auth_header(user)


def _raise(*_a, **_k):
    raise PermissionError(DENIED)


_CASES = {
    "ebook": (make_ebook, "_write_ebook_metadata"),
    "audiobook": (make_audiobook, "_write_audiobook_metadata"),
}


@pytest.mark.parametrize("media", ["ebook", "audiobook"])
async def test_failed_write_back_is_reported_and_row_kept(db, editor_client, monkeypatch, media):
    import routers.library as library

    make, writer = _CASES[media]
    monkeypatch.setattr(library, writer, _raise)
    row = await make(db)
    c, headers = editor_client

    r = await c.patch(f"/api/library/{media}s/{row.id}", json={"title": "New Title"}, headers=headers)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["write_back"] == {"ok": False, "error": DENIED}
    assert body["title"] == "New Title"

    got = await c.get(f"/api/library/{media}s/{row.id}", headers=headers)
    assert got.status_code == 200, got.text
    assert got.json()["title"] == "New Title"
    assert got.json().get("write_back") is None


@pytest.mark.parametrize("media", ["ebook", "audiobook"])
async def test_successful_write_back_is_reported_ok(db, editor_client, monkeypatch, media):
    import routers.library as library

    make, writer = _CASES[media]
    monkeypatch.setattr(library, writer, lambda path, book: None)
    row = await make(db)
    c, headers = editor_client

    r = await c.patch(f"/api/library/{media}s/{row.id}", json={"title": "New Title"}, headers=headers)

    assert r.status_code == 200, r.text
    assert r.json()["write_back"] == {"ok": True, "error": None}


@pytest.mark.skipif(
    os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="needs POSIX permissions and a non-root user",
)
async def test_real_unwritable_epub_directory_is_reported(db, editor_client, tmp_path):
    book_dir = tmp_path / "lib"
    book_dir.mkdir()
    path = write_epub(str(book_dir / "b.epub"), [("c1.xhtml", "<html><body><p>Hi.</p></body></html>")])
    with open(path, "rb") as fh:
        before = fh.read()
    row = await make_ebook(db)
    row.file_path = path
    await db.commit()
    c, headers = editor_client

    os.chmod(book_dir, 0o555)  # the atomic replace needs to create a temp file here
    try:
        r = await c.patch(f"/api/library/ebooks/{row.id}", json={"title": "New Title"}, headers=headers)
    finally:
        os.chmod(book_dir, 0o755)

    assert r.status_code == 200, r.text
    assert r.json()["write_back"]["ok"] is False
    with open(path, "rb") as fh:
        assert fh.read() == before
