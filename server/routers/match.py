import logging
import os
from pathlib import Path
from typing import List, Optional
import httpx

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from sqlalchemy.future import select
from config import settings
from database import get_db
from schemas import MatchRequest, MatchResult
from models.book import EBook, AudioBook
from models.user import User
from routers.auth import get_current_user
from routers.library import sanitize_filename

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/match",
    tags=["match"],
)

async def fetch_google_books(query: str, author: Optional[str]) -> List[MatchResult]:
    """Search Google Books API."""
    # Build query string
    q = query
    if author:
        q += f"+inauthor:{author}"
        
    url = f"https://www.googleapis.com/books/v1/volumes?q={q}&maxResults=10"
    if hasattr(settings, 'google_books_api_key') and settings.google_books_api_key:
        url += f"&key={settings.google_books_api_key}"
    
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, timeout=10.0)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"Google Books API HTTP error: {e}")
            if e.response.status_code == 429:
                raise HTTPException(status_code=429, detail="Google Books rate limit exceeded. Try Open Library or add an API key.")
            raise HTTPException(status_code=502, detail="External provider error")
        except Exception as e:
            logger.error(f"Google Books API connection error: {e}")
            raise HTTPException(status_code=502, detail="External provider error")
            
    results = []
    for item in data.get("items", []):
        vol = item.get("volumeInfo", {})
        
        # Parse fields
        title = vol.get("title", "Unknown Title")
        authors = vol.get("authors", [])
        author_str = ", ".join(authors) if authors else None
        
        # Publish year
        pub_date = vol.get("publishedDate", "")
        pub_year = None
        if pub_date:
            try:
                pub_year = int(pub_date[:4])
            except ValueError:
                pass
                
        # ISBN
        isbn = None
        for identifier in vol.get("industryIdentifiers", []):
            if identifier.get("type") == "ISBN_13":
                isbn = identifier.get("identifier")
                break
            elif identifier.get("type") == "ISBN_10" and not isbn:
                isbn = identifier.get("identifier")
                
        # High res cover URL hack (zoom=1 instead of zoom=5)
        cover_url = None
        image_links = vol.get("imageLinks", {})
        if "thumbnail" in image_links:
            cover_url = image_links["thumbnail"].replace("zoom=1", "zoom=0").replace("http:", "https:")
            
        results.append(MatchResult(
            id=item.get("id", ""),
            title=title,
            author=author_str,
            publish_year=pub_year,
            publisher=vol.get("publisher"),
            description=vol.get("description"),
            isbn=isbn,
            cover_url=cover_url
        ))
        
    return results

async def fetch_open_library(query: str, author: Optional[str]) -> List[MatchResult]:
    """Search Open Library API."""
    # Build query
    params = {"q": query, "limit": 10}
    if author:
        params["author"] = author
        
    url = "https://openlibrary.org/search.json"
    
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, params=params, timeout=10.0)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"Open Library API error: {e}")
            raise HTTPException(status_code=502, detail="External provider error")
            
    results = []
    for doc in data.get("docs", []):
        # Open library returns arrays for many single fields
        title = doc.get("title", "Unknown Title")
        
        authors = doc.get("author_name", [])
        author_str = ", ".join(authors) if authors else None
        
        pub_years = doc.get("publish_year", [])
        pub_year = pub_years[0] if pub_years else None
        
        publishers = doc.get("publisher", [])
        publisher = publishers[0] if publishers else None
        
        isbns = doc.get("isbn", [])
        isbn = isbns[0] if isbns else None
        
        cover_url = None
        cover_i = doc.get("cover_i")
        if cover_i:
            cover_url = f"https://covers.openlibrary.org/b/id/{cover_i}-L.jpg"
            
        results.append(MatchResult(
            id=doc.get("key", ""),
            title=title,
            author=author_str,
            publish_year=pub_year,
            publisher=publisher,
            description=None, # OpenLibrary search doesn't return description easily
            isbn=isbn,
            cover_url=cover_url
        ))
        
    return results


@router.post("/search", response_model=List[MatchResult])
async def search_metadata(
    req: MatchRequest,
    _: User = Depends(get_current_user)
):
    """Search external providers for book metadata."""
    if req.provider == "google":
        return await fetch_google_books(req.query, req.author)
    elif req.provider == "openlibrary":
        return await fetch_open_library(req.query, req.author)
    else:
        raise HTTPException(status_code=400, detail="Invalid provider")

from pydantic import BaseModel

class ApplyCoverRequest(BaseModel):
    book_type: str
    book_id: int
    cover_url: str

@router.post("/apply-cover")
async def apply_remote_cover(
    req: ApplyCoverRequest,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user)
):
    """Download a remote cover URL and apply it to a book."""
    if req.book_type not in ["ebook", "audiobook"]:
        raise HTTPException(status_code=400, detail="Invalid book_type")
        
    model = EBook if req.book_type == "ebook" else AudioBook
    result = await db.execute(select(model).filter(model.id == req.book_id))
    book = result.scalar_one_or_none()
    
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
        
    # Download the image
    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            resp = await client.get(req.cover_url, timeout=60.0)
            resp.raise_for_status()
            content = resp.content
        except Exception as e:
            logger.error(f"Failed to download remote cover: {repr(e)}")
            raise HTTPException(status_code=502, detail="Failed to fetch cover from remote URL")
            
    # Save the file
    covers_path = Path(settings.covers_dir)
    covers_path.mkdir(parents=True, exist_ok=True)
    
    title_safe = sanitize_filename(book.title or "Unknown Title")
    author_safe = sanitize_filename(book.author or "Unknown Author")
    ext = ".jpg" # Mostly jpgs from these APIs
    filename = f"{author_safe} - {title_safe}{ext}"
    
    # Handle collisions
    base_name = filename[:-4]
    counter = 1
    while (covers_path / filename).exists():
        filename = f"{base_name}_{counter}{ext}"
        counter += 1
        
    file_path = covers_path / filename
    
    try:
        with open(file_path, "wb") as f:
            f.write(content)
    except Exception as e:
        logger.error(f"Failed to save matched cover: {e}")
        raise HTTPException(status_code=500, detail="Failed to save cover file")
        
    # Remove old cover if it exists
    if book.cover_path:
        old_path = Path(book.cover_path)
        if old_path.exists() and old_path != file_path:
            try:
                old_path.unlink()
            except IOError as e:
                logger.warning(f"Failed to delete old cover {old_path}: {e}")
                
    # Update DB
    url_path = f"/api/files/covers/{filename}"
    book.cover_path = url_path
    await db.commit()
    
    return {"message": "Cover applied successfully", "cover_path": url_path}
