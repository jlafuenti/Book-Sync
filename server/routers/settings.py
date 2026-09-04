import secrets
from typing import Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from models.settings import SystemSetting
from database import get_db
from routers.auth import get_current_user, get_admin_user
from models.user import User
from services import credentials as credential_store
from services import offhours
from services.filename_patterns import PatternError, validate_patterns
from services.url_safety import assert_safe_url, UnsafeUrlError

router = APIRouter(prefix="/api/settings", tags=["settings"])

# Placeholder returned by GET /api/settings for secrets that exist in the
# encrypted credential store. The web UI uses presence of this string to know
# a credential is set without exposing the value.
_SECRET_PLACEHOLDER = "********"


# ---------------------------------------------------------------------------
# Bodies for the three "Test connection" endpoints (issue #284).
#
# These carried the ABS token, the Hardcover token and the Jetson key as query
# parameters, which put them in the request line -- and so into uvicorn's access
# log, which is durable. Unlike the media tokens on cover URLs, these are
# long-lived and not resource-scoped: they should never have been in a URL. A
# confirmed instance was found in the production container's log.
#
# POST with a body is the fix. The endpoints are reads in spirit, but a GET
# cannot carry a body, and correctness about where secrets travel beats REST
# purity here.
# ---------------------------------------------------------------------------

class TestAbsRequest(BaseModel):
    url: str
    token: str = ""


class TestHardcoverRequest(BaseModel):
    token: str = ""


class TestRemoteRequest(BaseModel):
    url: str
    key: str = ""

DEFAULT_SETTINGS = {
    "ebook_filename_patterns": [
        "<Author> - [<Series> <Book Number>] - <Title>",
        "[<Series> <Book Number>] <Title>",
        "<Author>/<Series>/<Book Number> - <Title>",
        "<Author>/<Title>",
        "<Title>"
    ],
    "audiobook_filename_patterns": [
        "<Author> - [<Series> <Book Number>] - <Title>",
        "[<Series> <Book Number>] <Title>",
        "<Author>/<Series>/<Book Number> - <Title>",
        "<Author>/<Series>/<Title>",
        "<Author>/<Title>",
        "<Title>"
    ],
    "transcription_provider": "remote_with_fallback",
    "transcription_remote_url": "",
    "transcription_remote_key": "",
    # Must match transcription_providers.DEFAULT_REMOTE_TIMEOUT_SEC, which owns
    # the knob's semantics (issue #248).
    "transcription_remote_timeout": 86400,
    # Retry ladder for a provider that is down (issue #242) — owned by
    # services/queue_manager.py. Defaults must match DEFAULT_RETRY_MAX and
    # DEFAULT_RETRY_BASE_SECONDS there. The delay doubles per retry (capped at
    # queue_manager.RETRY_DELAY_CAP_SECONDS), so 5 x 30 s spans ~15 minutes —
    # long enough to ride out a worker reboot and a model load.
    "transcription_retry_max": 5,
    "transcription_retry_base_seconds": 30,
    "auto_transcribe_enabled": False,
    "whisper_model": "medium",
    # Off-hours transcription window (issue #106) — owned by services/offhours.py.
    # Defaults must match offhours.DEFAULTS. Disabled by default so transcription
    # keeps dispatching 24/7 until an admin opts in.
    "transcription_offhours_enabled": False,
    "transcription_offhours_start": "01:00",
    "transcription_offhours_end": "07:00",
    "transcription_offhours_timezone": "UTC",
    "abs_enabled": False,
    "abs_url": "",
    "abs_api_token": "",
    "abs_audiobooks_prefix": "",
    "hardcover_api_token": "",
    # Backups (issue #60) — owned by services/backup_service.py. Defaults must
    # match backup_service._DEFAULTS.
    "backup_enabled": True,
    "backup_hour": 3,
    "backup_keep_daily": 14,
    "backup_keep_monthly": 6,
    # Audit-log retention in days (issue #261) — owned by
    # services/audit_retention.py. Default must match
    # audit_retention.DEFAULT_RETENTION_DAYS. 0 means keep forever.
    "audit_log_retention_days": 90,
}

@router.get("/", response_model=Dict[str, Any])
async def get_settings(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    """Get all system settings."""
    result = await db.execute(select(SystemSetting))
    settings_list = result.scalars().all()
    
    settings_dict = {}
    for s in settings_list:
        if s.key in ["ebook_filename_patterns", "audiobook_filename_patterns", "filename_patterns"]:
             settings_dict[s.key] = s.value.split("\n") if s.value else []
        else:
            # Cast back to correct type based on DEFAULT_SETTINGS
            if s.key in DEFAULT_SETTINGS:
                default_type = type(DEFAULT_SETTINGS[s.key])
                if default_type is bool:
                    settings_dict[s.key] = str(s.value).lower() == "true"
                elif default_type is int:
                    try:
                        settings_dict[s.key] = int(s.value)
                    except ValueError:
                        settings_dict[s.key] = DEFAULT_SETTINGS[s.key]
                else:
                    settings_dict[s.key] = s.value
            else:
                settings_dict[s.key] = s.value
            
    # merge with defaults if missing
    for key, val in DEFAULT_SETTINGS.items():
        if key not in settings_dict:
            settings_dict[key] = val

    # Mask abs_api_token: never expose the value. Show a placeholder if a
    # credential is on file (encrypted store), empty string otherwise.
    abs_token = await credential_store.get_credential(db, "abs")
    settings_dict["abs_api_token"] = _SECRET_PLACEHOLDER if abs_token else ""

    # Same masking for the Jetson shared secret.
    remote_key = await credential_store.get_credential(db, "transcription_remote")
    settings_dict["transcription_remote_key"] = _SECRET_PLACEHOLDER if remote_key else ""

    # And for the Hardcover metadata-provider token.
    hardcover_token = await credential_store.get_credential(db, "hardcover")
    settings_dict["hardcover_api_token"] = _SECRET_PLACEHOLDER if hardcover_token else ""

    return settings_dict

@router.put("/", response_model=Dict[str, Any])
async def update_settings(
    new_settings: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user)
):
    """Update system settings."""
    # Validate the off-hours window up front, against the values already on
    # file — a partial edit ("just move the start time") can still produce an
    # ambiguous start == end window. Raising here means a rejected PUT writes
    # nothing at all, including the valid keys sent alongside it.
    if set(offhours.DEFAULTS) & set(new_settings):
        stored_rows = await db.execute(
            select(SystemSetting).where(SystemSetting.key.in_(tuple(offhours.DEFAULTS)))
        )
        stored = {s.key: s.value for s in stored_rows.scalars().all()}
        try:
            offhours.validate_settings(new_settings, stored=stored)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # Filename patterns are validated here, once, instead of failing per file
    # during a scan (issue #354). The old failure mode was a warning per file
    # for every file in the library and no metadata from any of them — visible
    # only to whoever read the log. Rejected before anything is written, so a
    # bad pattern takes the rest of the PUT down with it rather than half-saving.
    _PATTERN_KEYS = ("ebook_filename_patterns", "audiobook_filename_patterns")
    for key in _PATTERN_KEYS:
        if key not in new_settings:
            continue
        value = new_settings[key]
        patterns = value if isinstance(value, list) else str(value).split("\n")
        try:
            validate_patterns(patterns)
        except PatternError as e:
            raise HTTPException(status_code=422, detail=f"{key}: {e}")

    # The remote timeout is honoured as stored since #248, so a typo here would
    # abandon every transcription seconds in. Rejected before anything is
    # written, so a bad value takes the rest of the PUT down with it.
    if "transcription_remote_timeout" in new_settings:
        from services.transcription_providers import MIN_REMOTE_TIMEOUT_SEC

        try:
            timeout = int(new_settings["transcription_remote_timeout"])
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=422,
                detail="transcription_remote_timeout must be a whole number of seconds",
            )
        if timeout < MIN_REMOTE_TIMEOUT_SEC:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"transcription_remote_timeout must be at least "
                    f"{MIN_REMOTE_TIMEOUT_SEC}s — the worker request blocks for "
                    f"the whole transcription, so a short timeout fails every job"
                ),
            )

    for key, value in new_settings.items():
        # Route abs_api_token through the encrypted credential store. An empty
        # string clears the credential; the placeholder means "leave unchanged"
        # (so a UI that round-trips the masked GET doesn't wipe the secret).
        if key == "abs_api_token":
            if value is None or value == _SECRET_PLACEHOLDER:
                continue
            if value == "":
                await credential_store.delete_credential(db, "abs")
            else:
                await credential_store.set_credential(db, "abs", str(value))
            continue

        if key == "transcription_remote_key":
            if value is None or value == _SECRET_PLACEHOLDER:
                continue
            if value == "":
                await credential_store.delete_credential(db, "transcription_remote")
            else:
                await credential_store.set_credential(db, "transcription_remote", str(value))
            continue

        if key == "hardcover_api_token":
            if value is None or value == _SECRET_PLACEHOLDER:
                continue
            if value == "":
                await credential_store.delete_credential(db, "hardcover")
            else:
                await credential_store.set_credential(db, "hardcover", str(value))
            continue

        # Serialize before saving
        if key in ["ebook_filename_patterns", "audiobook_filename_patterns"] and isinstance(value, list):
            value = "\n".join(value)
        else:
            value = str(value)

        result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
        setting = result.scalar_one_or_none()

        if setting:
            setting.value = value
        else:
            setting = SystemSetting(key=key, value=value)
            db.add(setting)

    await db.commit()
    return await get_settings(db)

@router.post("/test-abs")
async def test_abs_connection(
    body: TestAbsRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    """Test the connection to an Audiobookshelf server."""
    import httpx

    url, token = body.url, body.token

    if not url:
        raise HTTPException(status_code=400, detail="URL is required")

    try:
        assert_safe_url(url, allow_private=True)
    except UnsafeUrlError as e:
        raise HTTPException(status_code=400, detail=f"Invalid URL: {e}")

    # Prefer an explicitly-passed token over the one already on file, same
    # fallback as test-remote. A UI that round-trips the masked GET value
    # (the "********" placeholder) or leaves the field blank should still
    # test against the real stored credential, not fail or send the mask.
    api_token = token if (token and token != _SECRET_PLACEHOLDER) else await credential_store.get_credential(db, "abs")
    if not api_token:
        raise HTTPException(status_code=400, detail="API token is required")

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(
                f"{url.rstrip('/')}/api/libraries",
                headers={"Authorization": f"Bearer {api_token}"},
            )
            r.raise_for_status()
            libraries = r.json().get("libraries", [])
            book_libs = [lib["name"] for lib in libraries if lib.get("mediaType") == "book"]
            return {
                "success": True,
                "library_count": len(libraries),
                "book_libraries": book_libs,
            }
    except httpx.RequestError as e:
        raise HTTPException(status_code=400, detail=f"Connection failed: {str(e)}")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            raise HTTPException(status_code=400, detail="Authentication failed — check your API token")
        raise HTTPException(status_code=400, detail=f"Server returned HTTP {e.response.status_code}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@router.post("/test-hardcover")
async def test_hardcover_connection(
    body: TestHardcoverRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    """Validate a Hardcover API token by running the trivial `me` query.
    Same placeholder/stored-credential fallback semantics as test-abs."""
    import httpx

    token = body.token

    api_token = token if (token and token != _SECRET_PLACEHOLDER) else await credential_store.get_credential(db, "hardcover")
    if not api_token:
        raise HTTPException(status_code=400, detail="API token is required")

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.post(
                "https://api.hardcover.app/v1/graphql",
                json={"query": "query { me { username } }"},
                headers={"Authorization": f"Bearer {api_token}"},
            )
            r.raise_for_status()
            body = r.json()
    except httpx.RequestError as e:
        raise HTTPException(status_code=400, detail=f"Connection failed: {str(e)}")
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (401, 403):
            raise HTTPException(status_code=400, detail="Authentication failed — check your API token")
        raise HTTPException(status_code=400, detail=f"Server returned HTTP {e.response.status_code}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

    # Hasura reports a bad token as a 200 with an errors array.
    me = (body.get("data") or {}).get("me") or []
    if body.get("errors") or not me:
        raise HTTPException(status_code=400, detail="Authentication failed — check your API token")

    return {"success": True, "username": me[0].get("username")}


@router.post("/transcription-remote-key/generate")
async def generate_transcription_remote_key(
    db: AsyncSession = Depends(get_db), _: User = Depends(get_admin_user)
):
    """
    Generate and persist a new shared secret for the Jetson transcription
    server. Returned once, in plaintext, so the admin can copy it into the
    Jetson's TRANSCRIPTION_API_KEY — it's stored encrypted from here on and
    GET /api/settings only ever returns the masked placeholder for it.
    """
    key = secrets.token_urlsafe(32)
    await credential_store.set_credential(db, "transcription_remote", key)
    await db.commit()
    return {"key": key}


@router.post("/test-remote")
async def test_remote_connection(
    body: TestRemoteRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    """
    Test the connection to a remote transcription server from the backend.
    This avoids CORS and VPN routing issues where the frontend browser
    cannot reach a local LAN IP like the Jetson directly.
    """
    import httpx

    url, key = body.url, body.key

    if not url:
        raise HTTPException(status_code=400, detail="URL is required")

    try:
        assert_safe_url(url, allow_private=True)
    except UnsafeUrlError as e:
        raise HTTPException(status_code=400, detail=f"Invalid URL: {e}")

    # Prefer an explicitly-passed key (e.g. just generated but not yet saved
    # to the URL field's sibling state) over the one already on file.
    api_key = key or await credential_store.get_credential(db, "transcription_remote")
    if not api_key:
        raise HTTPException(status_code=400, detail="Remote Server API Key is required")

    full_url = url if url.endswith("/v1/health") else f"{url.rstrip('/')}/v1/health"

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                full_url, headers={"Authorization": f"Bearer {api_key}"}
            )
            response.raise_for_status()
            data = response.json()
            return {
                "success": True,
                "gpu_available": data.get("gpu_available", False),
                "gpu_name": data.get("gpu_name"),
                "model_loaded": data.get("model_loaded", False),
                # Since #106 the worker loads on demand and unloads when idle,
                # so "not loaded" is a resting state rather than a fault. Older
                # workers don't send this — the UI falls back to model_loaded.
                "model_state": data.get("model_state"),
            }
    except httpx.RequestError as e:
        raise HTTPException(status_code=400, detail=f"Connection failed: {str(e)}")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            raise HTTPException(status_code=400, detail="Authentication failed — check the Remote Server API Key")
        raise HTTPException(status_code=400, detail=f"Server returned HTTP {e.response.status_code}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Integration error: {str(e)}")
