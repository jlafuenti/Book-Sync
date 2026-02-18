from typing import Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from models.settings import SystemSetting
from database import get_db
from routers.auth import get_current_user
from models.user import User

router = APIRouter(prefix="/api/settings", tags=["settings"])

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
    ]
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
            settings_dict[s.key] = s.value
            
    # merge with defaults if missing
    for key, val in DEFAULT_SETTINGS.items():
        if key not in settings_dict:
            settings_dict[key] = val
            
    return settings_dict

@router.put("/", response_model=Dict[str, Any])
async def update_settings(
    new_settings: Dict[str, Any], 
    db: AsyncSession = Depends(get_db), 
    _: User = Depends(get_current_user)
):
    """Update system settings."""
    for key, value in new_settings.items():
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
