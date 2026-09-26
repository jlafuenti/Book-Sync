"""Issue #730: an ebook's printed page count, for the reader's "actual pages"."""
import pytest

from routers import library
from tests.factories import make_ebook


@pytest.fixture
async def editor_client(make_client, make_user, auth_header):
    """A client authenticated as an editor, wired to the real library router.

    The plan's brief called for the shared `client` fixture (conftest.py), but
    that fixture only mounts `auth` and `sync` -- `/api/library/...` 404s on
    it. `make_client` + `library.router` is the pattern
    `test_metadata_fields_roundtrip.py` already uses for this same endpoint.
    """
    user = await make_user(username="ed", role="editor")
    async with make_client(library.router) as c:
        yield c, auth_header(user)


async def _patch(c, headers, ebook_id, **body):
    return await c.patch(f"/api/library/ebooks/{ebook_id}", json=body, headers=headers)


async def test_an_editor_sets_the_print_page_count(db, editor_client, monkeypatch):
    monkeypatch.setattr(library, "_write_ebook_metadata", lambda *a, **k: None)
    c, headers = editor_client
    ebook = await make_ebook(db, title="Axis Test")

    resp = await _patch(c, headers, ebook.id, print_page_count=412)

    assert resp.status_code == 200, resp.text
    assert resp.json()["print_page_count"] == 412


async def test_omitting_it_leaves_it_alone_and_zero_clears_it(db, editor_client, monkeypatch):
    monkeypatch.setattr(library, "_write_ebook_metadata", lambda *a, **k: None)
    c, headers = editor_client
    ebook = await make_ebook(db, title="Axis Test", print_page_count=300)

    kept = await _patch(c, headers, ebook.id, title="Axis Test 2")
    assert kept.json()["print_page_count"] == 300
    cleared = await _patch(c, headers, ebook.id, print_page_count=0)
    assert cleared.json()["print_page_count"] is None


async def test_a_negative_or_absurd_count_is_refused(db, editor_client):
    c, headers = editor_client
    ebook = await make_ebook(db, title="Axis Test")

    assert (await _patch(c, headers, ebook.id, print_page_count=-3)).status_code == 422
    assert (await _patch(c, headers, ebook.id, print_page_count=100001)).status_code == 422


async def test_a_plain_user_cannot_set_it(db, make_client, make_user, auth_header):
    user = await make_user(username="reader")
    ebook = await make_ebook(db, title="Axis Test")

    async with make_client(library.router) as c:
        resp = await c.patch(
            f"/api/library/ebooks/{ebook.id}",
            json={"print_page_count": 10},
            headers=auth_header(user),
        )
    assert resp.status_code == 403
