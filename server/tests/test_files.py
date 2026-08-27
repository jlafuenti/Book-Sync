"""Media-token auth tests for the file-serving router (issue #50).

Covers get_user_for_cover / get_user_for_audiobook: a full access token via
the Authorization header still works, a token scoped to the exact requested
resource via ?token= works, and every way a query-param token can be wrong
(full access token, wrong resource, expired, stale token_version) is rejected.
"""

import contextlib
from datetime import timedelta
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from config import settings
from models.book import AudioBook
from models.user import User
from routers.auth import create_access_token, create_media_token, create_token
import routers.files as files


@contextlib.asynccontextmanager
async def _files_client():
    app = FastAPI()
    app.include_router(files.router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def temp_covers_dir(monkeypatch, tmp_path):
    covers_dir = tmp_path / "covers"
    covers_dir.mkdir()
    monkeypatch.setattr(settings, "covers_dir", str(covers_dir))
    return covers_dir


async def _make_audiobook(db, file_path):
    book = AudioBook(title="A Book", filename="a.m4b", file_path=str(file_path))
    db.add(book)
    await db.commit()
    await db.refresh(book)
    return book


# --- Cover serving: get_user_for_cover --------------------------------------

async def test_get_cover_accepts_full_access_token_via_header(
    make_user, auth_header, temp_covers_dir,
):
    (temp_covers_dir / "cover.jpg").write_bytes(b"jpeg-bytes")
    user = await make_user(username="reader", role="user")

    async with _files_client() as client:
        r = await client.get("/api/files/covers/cover.jpg", headers=auth_header(user))

    assert r.status_code == 200
    assert r.content == b"jpeg-bytes"


async def test_get_cover_accepts_scoped_media_token_via_query(
    make_user, temp_covers_dir,
):
    (temp_covers_dir / "cover.jpg").write_bytes(b"jpeg-bytes")
    user = await make_user(username="reader", role="user")
    token = create_media_token(user, "cover", "cover.jpg")

    async with _files_client() as client:
        r = await client.get(f"/api/files/covers/cover.jpg?token={token}")

    assert r.status_code == 200
    assert r.content == b"jpeg-bytes"


async def test_get_cover_with_hash_in_filename_accepts_scoped_token(
    make_user, temp_covers_dir,
):
    """Issue #126: a '#' (or space) in the cover name must survive percent-encoding
    on the wire -- the decoded path param has to match the media token's
    resource_id, which is minted from the raw filename."""
    name = "James_Patterson - Private_#1_Suspect.jpg"
    (temp_covers_dir / name).write_bytes(b"jpeg-bytes")
    user = await make_user(username="reader", role="user")
    token = create_media_token(user, "cover", name)

    async with _files_client() as client:
        r = await client.get(f"/api/files/covers/{quote(name)}?token={token}")

    assert r.status_code == 200
    assert r.content == b"jpeg-bytes"


async def test_get_cover_rejects_full_access_token_via_query(
    make_user, temp_covers_dir,
):
    """The core regression test for #50: a full access token in ?token= must
    no longer work -- only a resource-scoped media token is accepted there."""
    (temp_covers_dir / "cover.jpg").write_bytes(b"jpeg-bytes")
    user = await make_user(username="reader", role="user")
    access_token = create_access_token(user)

    async with _files_client() as client:
        r = await client.get(f"/api/files/covers/cover.jpg?token={access_token}")

    assert r.status_code == 401


async def test_get_cover_rejects_media_token_for_different_resource(
    make_user, temp_covers_dir,
):
    (temp_covers_dir / "cover.jpg").write_bytes(b"jpeg-bytes")
    user = await make_user(username="reader", role="user")
    token = create_media_token(user, "cover", "other-cover.jpg")

    async with _files_client() as client:
        r = await client.get(f"/api/files/covers/cover.jpg?token={token}")

    assert r.status_code == 401


async def test_get_cover_rejects_expired_media_token(make_user, temp_covers_dir):
    (temp_covers_dir / "cover.jpg").write_bytes(b"jpeg-bytes")
    user = await make_user(username="reader", role="user")
    expired = create_token(
        {"sub": str(user.id), "type": "media", "resource_type": "cover",
         "resource_id": "cover.jpg", "ver": user.token_version},
        timedelta(minutes=-1),
    )

    async with _files_client() as client:
        r = await client.get(f"/api/files/covers/cover.jpg?token={expired}")

    assert r.status_code == 401


async def test_get_cover_rejects_media_token_with_stale_token_version(
    make_user, temp_covers_dir, db,
):
    """Simulates logout/password-change bumping token_version after the
    media token was minted."""
    (temp_covers_dir / "cover.jpg").write_bytes(b"jpeg-bytes")
    user = await make_user(username="reader", role="user")
    token = create_media_token(user, "cover", "cover.jpg")

    db_user = (await db.execute(
        select(User).where(User.id == user.id)
    )).scalar_one()
    db_user.token_version += 1
    await db.commit()

    async with _files_client() as client:
        r = await client.get(f"/api/files/covers/cover.jpg?token={token}")

    assert r.status_code == 401


async def test_get_cover_rejects_access_token_with_stale_token_version(
    make_user, auth_header, temp_covers_dir, db,
):
    """Issue #206: the header path never compared `ver`.

    Logout, self password-change and admin reset all bump `token_version`, which
    kills a stolen access token on every route that goes through
    `get_current_user` — but the media resolver only applied the compare to the
    `?token=` branch. So a leaked 24h access token kept streaming the library
    after the user had logged out or been reset, on exactly the two endpoints
    worth having for bulk download.
    """
    (temp_covers_dir / "cover.jpg").write_bytes(b"jpeg-bytes")
    user = await make_user(username="reader", role="user")
    header = auth_header(user)  # minted at the current token_version

    db_user = (await db.execute(
        select(User).where(User.id == user.id)
    )).scalar_one()
    db_user.token_version += 1
    await db.commit()

    async with _files_client() as client:
        r = await client.get("/api/files/covers/cover.jpg", headers=header)

    assert r.status_code == 401


async def test_get_cover_requires_some_auth(make_user, temp_covers_dir):
    (temp_covers_dir / "cover.jpg").write_bytes(b"jpeg-bytes")
    async with _files_client() as client:
        r = await client.get("/api/files/covers/cover.jpg")
    assert r.status_code == 401


# --- Audiobook streaming: get_user_for_audiobook ----------------------------

async def test_download_audiobook_accepts_full_access_token_via_header(
    make_user, auth_header, db, tmp_path,
):
    audio_file = tmp_path / "a.m4b"
    audio_file.write_bytes(b"audio-bytes")
    book = await _make_audiobook(db, audio_file)
    user = await make_user(username="listener", role="user")

    async with _files_client() as client:
        r = await client.get(f"/api/files/audiobook/{book.id}", headers=auth_header(user))

    assert r.status_code == 200
    assert r.content == b"audio-bytes"


async def test_download_audiobook_accepts_scoped_media_token_via_query(
    make_user, db, tmp_path,
):
    audio_file = tmp_path / "a.m4b"
    audio_file.write_bytes(b"audio-bytes")
    book = await _make_audiobook(db, audio_file)
    user = await make_user(username="listener", role="user")
    token = create_media_token(user, "audiobook", str(book.id))

    async with _files_client() as client:
        r = await client.get(f"/api/files/audiobook/{book.id}?token={token}")

    assert r.status_code == 200
    assert r.content == b"audio-bytes"


async def test_download_audiobook_rejects_full_access_token_via_query(
    make_user, db, tmp_path,
):
    audio_file = tmp_path / "a.m4b"
    audio_file.write_bytes(b"audio-bytes")
    book = await _make_audiobook(db, audio_file)
    user = await make_user(username="listener", role="user")
    access_token = create_access_token(user)

    async with _files_client() as client:
        r = await client.get(f"/api/files/audiobook/{book.id}?token={access_token}")

    assert r.status_code == 401


async def test_download_audiobook_rejects_media_token_for_different_audiobook(
    make_user, db, tmp_path,
):
    audio_file = tmp_path / "a.m4b"
    audio_file.write_bytes(b"audio-bytes")
    book = await _make_audiobook(db, audio_file)
    user = await make_user(username="listener", role="user")
    token = create_media_token(user, "audiobook", str(book.id + 1))

    async with _files_client() as client:
        r = await client.get(f"/api/files/audiobook/{book.id}?token={token}")

    assert r.status_code == 401


async def test_download_audiobook_rejects_access_token_with_stale_token_version(
    make_user, auth_header, db, tmp_path,
):
    """Issue #206, the streaming half — see the cover test above."""
    audio_file = tmp_path / "a.m4b"
    audio_file.write_bytes(b"audio-bytes")
    book = await _make_audiobook(db, audio_file)
    user = await make_user(username="listener", role="user")
    header = auth_header(user)

    db_user = (await db.execute(
        select(User).where(User.id == user.id)
    )).scalar_one()
    db_user.token_version += 1
    await db.commit()

    async with _files_client() as client:
        r = await client.get(f"/api/files/audiobook/{book.id}", headers=header)

    assert r.status_code == 401


async def test_media_endpoints_refuse_a_must_reset_user(
    make_user, auth_header, temp_covers_dir, db, tmp_path,
):
    """Issue #209: `_resolve_media_user` does its own token decoding rather than
    going through `get_current_user`, so a gate placed only in that dependency
    would leave covers and audio streaming open to the temporary credential."""
    (temp_covers_dir / "cover.jpg").write_bytes(b"jpeg-bytes")
    audio_file = tmp_path / "a.m4b"
    audio_file.write_bytes(b"audio-bytes")
    book = await _make_audiobook(db, audio_file)
    user = await make_user(username="temp", must_reset_password=True)
    header = auth_header(user)

    async with _files_client() as client:
        cover = await client.get("/api/files/covers/cover.jpg", headers=header)
        audio = await client.get(f"/api/files/audiobook/{book.id}", headers=header)

    assert cover.status_code == 403
    assert cover.json()["detail"] == "password_reset_required"
    assert audio.status_code == 403
