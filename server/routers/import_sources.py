"""
Import-sources REST API.

Endpoints:
  GET    /api/import/sources                      List all sources + state
  PUT    /api/import/sources/{key}/config         Update enabled/auto_sync_enabled/cadence_hours
  POST   /api/import/sources/{key}/sync           Trigger a one-shot sync
  GET    /api/import/sources/{key}/jobs           Recent ImportJob history

  POST   /api/import/audible/login/start          Start browser-paste OAuth
  POST   /api/import/audible/login/complete       Submit pasted redirect URL
  POST   /api/import/audible/disconnect           Clear stored credentials

  POST   /api/import/acsm/upload                  Upload a single .acsm or .epub
"""

import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.import_source import ImportSource, ImportJob
from models.user import User
from routers.auth import get_admin_user
from services import import_scheduler
from services.import_sources import get_source, list_sources
from services.import_sources.acsm import process_file as acsm_process_file
from services.import_sources.audible import AudibleSource

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/import", tags=["import"])


# ---- helpers ----------------------------------------------------------------

async def _get_or_create_state(db: AsyncSession, source_key: str) -> ImportSource:
    result = await db.execute(
        select(ImportSource).where(ImportSource.source_key == source_key)
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = ImportSource(source_key=source_key)
        db.add(row)
        await db.flush()
    return row


async def _serialize_source(db: AsyncSession, source_key: str) -> Dict[str, Any]:
    adapter = get_source(source_key)
    state = await _get_or_create_state(db, source_key)
    return {
        "source_key": source_key,
        "display_name": adapter.DISPLAY_NAME,
        "book_type": adapter.BOOK_TYPE,
        "supports_auto_sync": adapter.SUPPORTS_AUTO_SYNC,
        "connected": await adapter.is_connected(db),
        "enabled": state.enabled,
        "auto_sync_enabled": state.auto_sync_enabled,
        "cadence_hours": state.cadence_hours,
        "last_sync_at": state.last_sync_at.isoformat() if state.last_sync_at else None,
        "last_status": state.last_status,
        "last_message": state.last_message,
    }


# ---- generic source endpoints ----------------------------------------------

@router.get("/sources", response_model=List[Dict[str, Any]])
async def list_sources_endpoint(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    return [await _serialize_source(db, key) for key in list_sources().keys()]


class SourceConfigUpdate(BaseModel):
    enabled: bool | None = None
    auto_sync_enabled: bool | None = None
    cadence_hours: int | None = None


@router.put("/sources/{source_key}/config")
async def update_source_config(
    source_key: str,
    payload: SourceConfigUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    if source_key not in list_sources():
        raise HTTPException(404, f"Unknown source: {source_key}")
    state = await _get_or_create_state(db, source_key)
    if payload.enabled is not None:
        state.enabled = payload.enabled
    if payload.auto_sync_enabled is not None:
        state.auto_sync_enabled = payload.auto_sync_enabled
    if payload.cadence_hours is not None:
        if payload.cadence_hours < 1:
            raise HTTPException(400, "cadence_hours must be >= 1")
        state.cadence_hours = payload.cadence_hours
    await db.commit()
    return await _serialize_source(db, source_key)


@router.post("/sources/{source_key}/sync")
async def trigger_sync(
    source_key: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    if source_key not in list_sources():
        raise HTTPException(404, f"Unknown source: {source_key}")
    adapter = get_source(source_key)
    if not await adapter.is_connected(db):
        raise HTTPException(400, "Source is not connected.")
    await import_scheduler.trigger_now(source_key)
    return {"started": True}


@router.get("/sources/{source_key}/jobs")
async def list_jobs(
    source_key: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    if source_key not in list_sources():
        raise HTTPException(404, f"Unknown source: {source_key}")
    result = await db.execute(
        select(ImportJob)
        .where(ImportJob.source_key == source_key)
        .order_by(ImportJob.started_at.desc())
        .limit(20)
    )
    return [
        {
            "id": j.id,
            "status": j.status,
            "trigger": j.trigger,
            "started_at": j.started_at.isoformat() if j.started_at else None,
            "finished_at": j.finished_at.isoformat() if j.finished_at else None,
            "items_added": j.items_added,
            "items_skipped": j.items_skipped,
            "error_message": j.error_message,
            "detail": j.detail,
        }
        for j in result.scalars().all()
    ]


# ---- Audible-specific endpoints --------------------------------------------

class AudibleLoginStartResponse(BaseModel):
    login_url: str
    state_token: str


@router.post("/audible/login/start", response_model=AudibleLoginStartResponse)
async def audible_login_start(_: User = Depends(get_admin_user)):
    login_url, state = AudibleSource.start_login()
    return AudibleLoginStartResponse(login_url=login_url, state_token=state)


class AudibleLoginCompleteRequest(BaseModel):
    state_token: str
    response_url: str


@router.post("/audible/login/complete")
async def audible_login_complete(
    payload: AudibleLoginCompleteRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    try:
        await AudibleSource.complete_login(db, payload.state_token, payload.response_url)
    except Exception as e:
        raise HTTPException(400, f"Audible login failed: {e}")
    state = await _get_or_create_state(db, "audible")
    state.enabled = True
    await db.commit()
    return {"connected": True}


@router.post("/audible/disconnect")
async def audible_disconnect(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    await AudibleSource.disconnect(db)
    state = await _get_or_create_state(db, "audible")
    state.enabled = False
    state.auto_sync_enabled = False
    await db.commit()
    return {"connected": False}


# ---- ACSM upload endpoint --------------------------------------------------

@router.post("/acsm/upload")
async def acsm_upload(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    if not file.filename:
        raise HTTPException(400, "missing filename")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in (".acsm", ".epub"):
        raise HTTPException(400, "only .acsm and .epub are accepted")

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)

    try:
        result = await acsm_process_file(db, tmp_path, original_filename=file.filename)
        await db.commit()
    except Exception as e:
        logger.exception(f"[acsm/upload] {file.filename} failed: {e}")
        raise HTTPException(400, f"Could not process file: {e}")
    finally:
        try:
            tmp_path.unlink()
        except Exception:
            pass

    if result.get("status") == "added":
        # Trigger a library scan so the new file gets a DB row.
        try:
            from routers.library import scan_library_impl
            await scan_library_impl(db)
            await db.commit()
        except Exception as e:
            logger.exception(f"[acsm/upload] post-upload scan failed: {e}")

    return result
