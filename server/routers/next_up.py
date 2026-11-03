"""
"Next up" dismissals (issue #921).

The Next up rule is client-side; this router only stores and lists the series
a user removed, so every device on the account agrees.
"""

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.next_up_dismissal import NextUpDismissal
from models.user import User
from routers.auth import get_current_user
from utils import utcnow

router = APIRouter(prefix="/api/next-up", tags=["next-up"])

MAX_SERIES_LEN = 500


def _clean(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("series must not be blank")
    return value


class NextUpDismissalCreate(BaseModel):
    series: str

    @field_validator("series")
    @classmethod
    def _series_not_blank(cls, value: str) -> str:
        return _clean(value)


class NextUpDismissalResponse(BaseModel):
    series: str
    dismissed_at: datetime

    model_config = {"from_attributes": True}


def _key(series: str) -> str:
    return series.strip().casefold()


async def _find(db: AsyncSession, user: User, series: str):
    return (await db.execute(
        select(NextUpDismissal).where(
            NextUpDismissal.user_id == user.id,
            NextUpDismissal.series_key == _key(series),
        )
    )).scalar_one_or_none()


@router.get("/dismissals", response_model=List[NextUpDismissalResponse])
async def list_dismissals(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    rows = await db.execute(
        select(NextUpDismissal)
        .where(NextUpDismissal.user_id == user.id)
        .order_by(NextUpDismissal.dismissed_at.desc(), NextUpDismissal.id.desc())
    )
    return rows.scalars().all()


@router.post("/dismissals", response_model=NextUpDismissalResponse)
async def add_dismissal(
    body: NextUpDismissalCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    row = await _find(db, user, body.series)
    now = utcnow()
    if row is None:
        row = NextUpDismissal(
            user_id=user.id, series_key=_key(body.series),
            series=body.series, dismissed_at=now,
        )
        db.add(row)
    else:
        row.series = body.series
        row.dismissed_at = now
    await db.flush()
    return row


@router.delete("/dismissals", status_code=204)
async def remove_dismissal(
    series: str = Query(..., max_length=MAX_SERIES_LEN),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    if not series.strip():
        raise HTTPException(status_code=422, detail="series must not be blank")
    row = await _find(db, user, series)
    if row is None:
        raise HTTPException(status_code=404, detail="No such dismissal")
    await db.delete(row)
    await db.flush()
    return Response(status_code=204)
