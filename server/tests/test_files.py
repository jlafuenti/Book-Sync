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


async def test_get_cover_rejects_a_media_token_from_a_signed_out_device(
    make_user, temp_covers_dir, db,
):
    """Issue #250 reopened #206 from the other side.

    A per-device logout does not bump `token_version`, so `ver` alone can no
    longer tell whether the session behind a token is over. Media tokens carry
    the minting session as `sid`; without checking it here a signed-out browser
    would go on streaming covers and audio for the rest of the token's life.
    """
    from models.refresh_token import RefreshToken
    from utils import utcnow

    (temp_covers_dir / "cover.jpg").write_bytes(b"jpeg-bytes")
    user = await make_user(username="reader", role="user")
    session = RefreshToken(user_id=user.id, jti="session-a", device_id="device-a")
    db.add(session)
    await db.commit()

    token = create_media_token(user, "cover", "cover.jpg", session_jti="session-a")
    async with _files_client() as client:
        assert (await client.get(
            f"/api/files/covers/cover.jpg?token={token}"
        )).status_code == 200

        session.revoked_at = utcnow()
        await db.commit()

        r = await client.get(f"/api/files/covers/cover.jpg?token={token}")
    assert r.status_code == 401


async def test_get_cover_rejects_an_access_token_from_a_signed_out_device(
    make_user, temp_covers_dir, db,
):
    """The header branch of the same hole: the access token names the session
    too, and a device that has signed out must not keep the download
    endpoints."""
    from models.refresh_token import RefreshToken
    from utils import utcnow

    (temp_covers_dir / "cover.jpg").write_bytes(b"jpeg-bytes")
    user = await make_user(username="reader", role="user")
    session = RefreshToken(user_id=user.id, jti="session-b", device_id="device-b")
    db.add(session)
    await db.commit()

    header = {"Authorization": f"Bearer {create_access_token(user, 'session-b')}"}
    async with _files_client() as client:
        assert (await client.get(
            "/api/files/covers/cover.jpg", headers=header
        )).status_code == 200

        session.revoked_at = utcnow()
        await db.commit()

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


async def test_audiobook_range_with_bearer_returns_206(
    make_user, auth_header, db, tmp_path,
):
    """The contract Android's streaming player depends on (issue #171).

    A Bearer header and a Range request together must answer 206 with a
    Content-Range, because Media3's OkHttpDataSource seeks by asking for byte
    ranges and authenticates with the header — never with a `?token=` in the
    URL, which would end up in logcat. This already passes; it is here so that a
    future change to `_range_response` or `_resolve_media_user` cannot quietly
    take seeking (or streaming at all) away from the phone.
    """
    audio_file = tmp_path / "a.m4b"
    audio_file.write_bytes(b"0123456789")
    book = await _make_audiobook(db, audio_file)
    user = await make_user(username="streamer", role="user")

    headers = {**auth_header(user), "Range": "bytes=2-5"}
    async with _files_client() as client:
        r = await client.get(f"/api/files/audiobook/{book.id}", headers=headers)

    assert r.status_code == 206
    assert r.headers["content-range"] == "bytes 2-5/10"
    assert r.headers["accept-ranges"] == "bytes"
    assert r.headers["content-length"] == "4"
    assert r.content == b"2345"


async def test_audiobook_open_ended_range_with_bearer_streams_to_the_end(
    make_user, auth_header, db, tmp_path,
):
    """`bytes=N-` is what a player sends to resume mid-book."""
    audio_file = tmp_path / "a.m4b"
    audio_file.write_bytes(b"0123456789")
    book = await _make_audiobook(db, audio_file)
    user = await make_user(username="streamer2", role="user")

    headers = {**auth_header(user), "Range": "bytes=7-"}
    async with _files_client() as client:
        r = await client.get(f"/api/files/audiobook/{book.id}", headers=headers)

    assert r.status_code == 206
    assert r.headers["content-range"] == "bytes 7-9/10"
    assert r.content == b"789"


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
