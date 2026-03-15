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
    ],
    "transcription_provider": "remote_with_fallback",
    "transcription_remote_url": "",
    "transcription_remote_timeout": 86400,
    "auto_transcribe_enabled": False,
    "whisper_model": "medium",
    "abs_enabled": False,
    "abs_url": "",
    "abs_api_token": "",
    "abs_audiobooks_prefix": "",
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

@router.get("/test-abs")
async def test_abs_connection(url: str, token: str, _: User = Depends(get_current_user)):
    """Test the connection to an Audiobookshelf server."""
    import httpx

    if not url or not token:
        raise HTTPException(status_code=400, detail="URL and token are required")

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(
                f"{url.rstrip('/')}/api/libraries",
                headers={"Authorization": f"Bearer {token}"},
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


@router.get("/test-remote")
async def test_remote_connection(url: str, _: User = Depends(get_current_user)):
    """
    Test the connection to a remote transcription server from the backend.
    This avoids CORS and VPN routing issues where the frontend browser
    cannot reach a local LAN IP like the Jetson directly.
    """
    import httpx
    
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")
        
    full_url = url if url.endswith("/v1/health") else f"{url.rstrip('/')}/v1/health"
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(full_url)
            response.raise_for_status()
            data = response.json()
            return {
                "success": True,
                "gpu_available": data.get("gpu_available", False),
                "gpu_name": data.get("gpu_name"),
                "model_loaded": data.get("model_loaded", False)
            }
    except httpx.RequestError as e:
        raise HTTPException(status_code=400, detail=f"Connection failed: {str(e)}")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=400, detail=f"Server returned HTTP {e.response.status_code}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Integration error: {str(e)}")
