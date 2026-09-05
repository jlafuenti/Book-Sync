"""
The two library endpoints that leak host paths are editor-gated (issue #263).

`GET /api/library/debug-metadata/{book_type}/{book_id}` returns the absolute
`file_path` of a book, the absolute `library_root` it sits under, and the raw
extracted tags. `GET /api/library/verify` returns the absolute `file_path` of
every row whose source file has gone missing. Both were reachable by any
signed-in account, which handed a stranger with a reader login a map of the
operator's filesystem for no product reason — neither endpoint has ever had a
non-editor caller (the Verify control lives inside LibraryPage's Maintenance
menu, which is already `hasMinRole('editor')`).

`EBookResponse.file_path` / `AudioBookResponse.file_path` staying in the normal
list payloads is a separate, deliberate decision recorded in #263; these two are
the endpoints that exist only for maintenance.
"""

import pytest

from models.book import AudioBook, EBook


@pytest.fixture
async def library_client(make_client):
    from routers import library

    async with make_client(library.router) as c:
        yield c


@pytest.fixture
async def an_ebook(db):
    book = EBook(
        title="Bartleby",
        author="Herman Melville",
        filename="bartleby.epub",
        # Deliberately absent from disk: the endpoint reports file_exists=False
        # and still answers 200, which is all these role tests need.
        file_path="/nonexistent/library/bartleby.epub",
        format="epub",
    )
    db.add(book)
    await db.commit()
    await db.refresh(book)
    return book


@pytest.fixture
async def an_audiobook(db):
    book = AudioBook(
        title="Bartleby",
        author="Herman Melville",
        filename="bartleby.m4b",
        file_path="/nonexistent/library/bartleby.m4b",
        format="m4b",
    )
    db.add(book)
    await db.commit()
    await db.refresh(book)
    return book


# --- debug-metadata --------------------------------------------------------


async def test_debug_metadata_requires_editor(
    library_client, make_user, auth_header, an_ebook
):
    user = await make_user(username="reader", role="user")
    r = await library_client.get(
        f"/api/library/debug-metadata/ebook/{an_ebook.id}", headers=auth_header(user)
    )
    assert r.status_code == 403


async def test_debug_metadata_allowed_for_editor(
    library_client, make_user, auth_header, an_ebook
):
    editor = await make_user(username="ed", role="editor")
    r = await library_client.get(
        f"/api/library/debug-metadata/ebook/{an_ebook.id}", headers=auth_header(editor)
    )
    assert r.status_code == 200
    assert r.json()["file_path"] == an_ebook.file_path


async def test_debug_metadata_for_an_audiobook_requires_editor(
    library_client, make_user, auth_header, an_audiobook
):
    user = await make_user(username="reader2", role="user")
    denied = await library_client.get(
        f"/api/library/debug-metadata/audiobook/{an_audiobook.id}",
        headers=auth_header(user),
    )
    assert denied.status_code == 403

    admin = await make_user(username="boss", role="admin")
    allowed = await library_client.get(
        f"/api/library/debug-metadata/audiobook/{an_audiobook.id}",
        headers=auth_header(admin),
    )
    assert allowed.status_code == 200


async def test_debug_metadata_rejects_a_reader_before_it_looks_the_book_up(
    library_client, make_user, auth_header
):
    """403, not 404: a reader must not be able to probe which ids exist."""
    user = await make_user(username="reader3", role="user")
    r = await library_client.get(
        "/api/library/debug-metadata/ebook/999999", headers=auth_header(user)
    )
    assert r.status_code == 403


# --- verify ----------------------------------------------------------------


async def test_verify_requires_editor(library_client, make_user, auth_header, an_ebook):
    user = await make_user(username="reader4", role="user")
    r = await library_client.get("/api/library/verify", headers=auth_header(user))
    assert r.status_code == 403


async def test_verify_allowed_for_editor(
    library_client, make_user, auth_header, an_ebook, an_audiobook
):
    editor = await make_user(username="ed2", role="editor")
    r = await library_client.get("/api/library/verify", headers=auth_header(editor))

    assert r.status_code == 200
    body = r.json()
    assert [e["id"] for e in body["orphaned_ebooks"]] == [an_ebook.id]
    assert [a["id"] for a in body["orphaned_audiobooks"]] == [an_audiobook.id]
