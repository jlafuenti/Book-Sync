import html
import logging
import os
import re
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
from routers.auth import get_current_user, get_editor_user
from routers.library import sanitize_filename
from services import credentials as credential_store
from services.url_safety import assert_safe_url, UnsafeUrlError
from utils import resolve_cover_url

logger = logging.getLogger(__name__)

MAX_COVER_REDIRECTS = 5
MAX_COVER_BYTES = 25 * 1024 * 1024

_COVER_EXT_BY_CONTENT_TYPE = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

router = APIRouter(
    prefix="/match",
    tags=["match"],
)

def _strip_html(text: Optional[str]) -> Optional[str]:
    """Collapse an HTML blurb (Audible summaries are HTML) into plain text."""
    if not text:
        return None
    text = re.sub(r"(?i)</p>|<br\s*/?>", " ", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip() or None


def _join_unique(names) -> Optional[str]:
    """Comma-join strings, dropping blanks and duplicates but keeping order."""
    seen = []
    for n in names:
        if n and n not in seen:
            seen.append(n)
    return ", ".join(seen) if seen else None


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
            cover_url=cover_url,
            genres=_join_unique(vol.get("categories", [])),
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
            cover_url=cover_url,
            tags=_join_unique(doc.get("subject", [])[:10]),
        ))

    return results


async def fetch_audible(query: str, author: Optional[str]) -> List[MatchResult]:
    """Search Audible's catalog API — the same source Audiobookshelf's Audible
    provider uses. Unauthenticated; reliably carries series name + sequence."""
    params = {
        "keywords": query,
        "num_results": 10,
        "products_sort_by": "Relevance",
        "response_groups": "contributors,product_desc,product_extended_attrs,"
                           "product_attrs,media,series,category_ladders",
        "image_sizes": "500,1024",
    }
    if author:
        params["author"] = author

    url = "https://api.audible.com/1.0/catalog/products"
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, params=params, timeout=10.0)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"Audible API error: {e}")
            raise HTTPException(status_code=502, detail="External provider error")

    results = []
    for product in data.get("products", []):
        series_name = None
        series_index = None
        for s in product.get("series", []) or []:
            if s.get("title"):
                series_name = s["title"]
                try:
                    series_index = float(s.get("sequence"))
                except (TypeError, ValueError):
                    series_index = None
                break

        pub_year = None
        release_date = product.get("release_date") or ""
        if release_date[:4].isdigit():
            pub_year = int(release_date[:4])

        genre_names = []
        for ladder in product.get("category_ladders", []) or []:
            for entry in ladder.get("ladder", []) or []:
                genre_names.append(entry.get("name"))

        images = product.get("product_images") or {}
        cover_url = images.get("1024") or images.get("500")

        runtime_min = product.get("runtime_length_min")
        language = product.get("language")

        results.append(MatchResult(
            id=product.get("asin", ""),
            title=product.get("title", "Unknown Title"),
            author=_join_unique(a.get("name") for a in product.get("authors", []) or []),
            narrators=_join_unique(n.get("name") for n in product.get("narrators", []) or []),
            series=series_name,
            series_index=series_index,
            publish_year=pub_year,
            publisher=product.get("publisher_name"),
            description=_strip_html(product.get("publisher_summary")
                                    or product.get("merchandising_summary")),
            genres=_join_unique(genre_names),
            language=language.capitalize() if language else None,
            duration_seconds=runtime_min * 60 if runtime_min else None,
            asin=product.get("asin"),
            cover_url=cover_url,
        ))

    return results


_HARDCOVER_URL = "https://api.hardcover.app/v1/graphql"

_HARDCOVER_SEARCH_QUERY = """
query SearchBooks($q: String!) {
  search(query: $q, query_type: "Book", per_page: 10, page: 1) {
    results
  }
}
"""


async def fetch_hardcover(query: str, author: Optional[str], db) -> List[MatchResult]:
    """Search Hardcover's GraphQL API. The Typesense search document already
    carries series, genres, tags, description, year, ISBNs and cover in one
    call. Requires the user-supplied API token (System settings)."""
    token = await credential_store.get_credential(db, "hardcover")
    if not token:
        raise HTTPException(
            status_code=400,
            detail="Hardcover API token not configured — add it under System → Hardcover Integration.",
        )

    q = f"{query} {author}" if author else query
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                _HARDCOVER_URL,
                json={"query": _HARDCOVER_SEARCH_QUERY, "variables": {"q": q}},
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"Hardcover API error: {e}")
            raise HTTPException(status_code=502, detail="External provider error")

    if data.get("errors"):
        logger.error(f"Hardcover GraphQL errors: {data['errors']}")
        raise HTTPException(status_code=502, detail="External provider error")

    search_results = (data.get("data") or {}).get("search", {}).get("results") or {}
    hits = search_results.get("hits", []) if isinstance(search_results, dict) else []

    results = []
    for hit in hits:
        doc = hit.get("document") or {}

        featured = doc.get("featured_series")
        series_name = None
        if isinstance(featured, dict):
            series_name = featured.get("series_name") or featured.get("name")
        if not series_name:
            names = doc.get("series_names") or []
            series_name = names[0] if names else None

        series_index = None
        raw_position = doc.get("featured_series_position")
        if raw_position is None and isinstance(featured, dict):
            raw_position = featured.get("position")
        try:
            series_index = float(raw_position)
        except (TypeError, ValueError):
            series_index = None

        image = doc.get("image")
        cover_url = image.get("url") if isinstance(image, dict) else image or None

        isbns = doc.get("isbns") or []

        results.append(MatchResult(
            id=str(doc.get("id", "")),
            title=doc.get("title", "Unknown Title"),
            author=_join_unique(doc.get("author_names") or []),
            series=series_name,
            series_index=series_index,
            publish_year=doc.get("release_year"),
            description=doc.get("description"),
            genres=_join_unique(doc.get("genres") or []),
            tags=_join_unique(doc.get("tags") or []),
            isbn=isbns[0] if isbns else None,
            cover_url=cover_url,
        ))

    return results


@router.post("/search", response_model=List[MatchResult])
async def search_metadata(
    req: MatchRequest,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user)
):
    """Search external providers for book metadata."""
    if req.provider == "google":
        return await fetch_google_books(req.query, req.author)
    elif req.provider == "openlibrary":
        return await fetch_open_library(req.query, req.author)
    elif req.provider == "audible":
        return await fetch_audible(req.query, req.author)
    elif req.provider == "hardcover":
        return await fetch_hardcover(req.query, req.author, db)
    else:
        raise HTTPException(status_code=400, detail="Invalid provider")

from pydantic import BaseModel

class ApplyCoverRequest(BaseModel):
    book_type: str
    book_id: int
    cover_url: str


async def _fetch_cover_safely(url: str) -> tuple[bytes, str]:
    """Fetch a remote cover image with an SSRF guard re-checked on every
    redirect hop (follow_redirects=True would connect to the redirect
    target before any hook could inspect it, so redirects are followed
    manually here instead), a content-type check, and a size cap enforced
    incrementally on the stream so an oversized response is aborted as
    soon as the cap is crossed rather than after being fully buffered."""
    current_url = url
    async with httpx.AsyncClient(follow_redirects=False, timeout=60.0) as client:
        for _ in range(MAX_COVER_REDIRECTS + 1):
            try:
                assert_safe_url(current_url, allow_private=False)
            except UnsafeUrlError as e:
                logger.warning(f"Rejected unsafe cover URL {current_url!r}: {e}")
                raise HTTPException(status_code=400, detail="Cover URL is not allowed")

            try:
                async with client.stream("GET", current_url) as resp:
                    if resp.is_redirect:
                        next_url = str(resp.next_request.url) if resp.next_request else resp.headers.get("location")
                        if not next_url:
                            raise HTTPException(status_code=502, detail="Invalid redirect from remote URL")
                        current_url = next_url
                        continue

                    resp.raise_for_status()

                    content_type = resp.headers.get("content-type", "")
                    if not content_type.split(";")[0].strip().lower().startswith("image/"):
                        raise HTTPException(status_code=400, detail="Remote URL did not return an image")

                    chunks = bytearray()
                    async for chunk in resp.aiter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > MAX_COVER_BYTES:
                            raise HTTPException(status_code=413, detail="Remote cover image too large")
                    return bytes(chunks), content_type
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to download remote cover: {repr(e)}")
                raise HTTPException(status_code=502, detail="Failed to fetch cover from remote URL")

    raise HTTPException(status_code=400, detail="Too many redirects fetching cover")


@router.post("/apply-cover")
async def apply_remote_cover(
    req: ApplyCoverRequest,
    db: Session = Depends(get_db),
    _: User = Depends(get_editor_user)
):
    """Download a remote cover URL and apply it to a book."""
    if req.book_type not in ["ebook", "audiobook"]:
        raise HTTPException(status_code=400, detail="Invalid book_type")

    model = EBook if req.book_type == "ebook" else AudioBook
    result = await db.execute(select(model).filter(model.id == req.book_id))
    book = result.scalar_one_or_none()

    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    content, content_type = await _fetch_cover_safely(req.cover_url)

    # Save the file
    covers_path = Path(settings.covers_dir)
    covers_path.mkdir(parents=True, exist_ok=True)
    
    title_safe = sanitize_filename(book.title or "Unknown Title")
    author_safe = sanitize_filename(book.author or "Unknown Author")
    ext = _COVER_EXT_BY_CONTENT_TYPE.get(content_type.split(";")[0].strip().lower(), ".jpg")
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
    old_path = resolve_cover_url(book.cover_path, covers_path)
    if old_path and old_path.is_file() and old_path != file_path:
        try:
            old_path.unlink()
        except OSError as e:
            logger.warning(f"Failed to delete old cover {old_path}: {e}")
                
    # Update DB
    url_path = f"/api/files/covers/{filename}"
    book.cover_path = url_path
    await db.commit()
    
    return {"message": "Cover applied successfully", "cover_path": url_path}
