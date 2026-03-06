"""
Pydantic schemas for API request/response validation.
"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, EmailStr, Field
from models.book import PairStatus
from models.bookmark import BookmarkSource


# ============================================================
# Auth Schemas
# ============================================================

class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=6)


class UserLogin(BaseModel):
    username: str
    password: str


class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    is_admin: bool
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class TokenRefresh(BaseModel):
    refresh_token: str


# ============================================================
# Book Schemas
# ============================================================

class EBookResponse(BaseModel):
    id: int
    title: str
    author: Optional[str]
    filename: str
    file_path: Optional[str] = None
    file_size: Optional[int]
    format: str
    series: Optional[str] = None
    series_index: Optional[float] = None
    metadata_source: Optional[str] = None
    metadata_pattern: Optional[str] = None
    uploaded_at: datetime
    # Extended metadata
    description: Optional[str] = None
    publisher: Optional[str] = None
    publish_year: Optional[int] = None
    language: Optional[str] = None
    genres: Optional[str] = None
    tags: Optional[str] = None
    isbn: Optional[str] = None
    asin: Optional[str] = None
    narrators: Optional[str] = None
    is_explicit: Optional[bool] = None
    is_abridged: Optional[bool] = None
    cover_path: Optional[str] = None

    class Config:
        from_attributes = True


class AudioBookResponse(BaseModel):
    id: int
    title: str
    author: Optional[str]
    filename: str
    file_path: Optional[str] = None
    file_size: Optional[int]
    duration_seconds: Optional[int]
    format: str
    series: Optional[str] = None
    series_index: Optional[float] = None
    metadata_source: Optional[str] = None
    metadata_pattern: Optional[str] = None
    uploaded_at: datetime
    # Extended metadata
    description: Optional[str] = None
    publisher: Optional[str] = None
    publish_year: Optional[int] = None
    language: Optional[str] = None
    genres: Optional[str] = None
    tags: Optional[str] = None
    isbn: Optional[str] = None
    asin: Optional[str] = None
    narrators: Optional[str] = None
    is_explicit: Optional[bool] = None
    is_abridged: Optional[bool] = None
    cover_path: Optional[str] = None

    class Config:
        from_attributes = True


class BookPairResponse(BaseModel):
    id: int
    ebook: EBookResponse
    audiobook: AudioBookResponse
    status: PairStatus
    matched_at: Optional[datetime]
    synced_at: Optional[datetime]

    class Config:
        from_attributes = True


class BookPairCreate(BaseModel):
    ebook_id: int
    audiobook_id: int


class PairedBookSummary(BaseModel):
    id: int
    title: str
    author: Optional[str]
    format: str

    class Config:
        from_attributes = True


class EBookDetailResponse(EBookResponse):
    paired_with: Optional[PairedBookSummary] = None
    pair_id: Optional[int] = None
    pair_status: Optional[PairStatus] = None


class AudioBookDetailResponse(AudioBookResponse):
    paired_with: Optional[PairedBookSummary] = None
    pair_id: Optional[int] = None
    pair_status: Optional[PairStatus] = None


# ============================================================
# Sync Map Schemas
# ============================================================

class SyncPointResponse(BaseModel):
    id: int
    epub_chapter: int
    epub_sentence_index: int
    epub_text_preview: Optional[str]
    audio_start_ms: int
    audio_end_ms: int
    audio_text: Optional[str] = None

    class Config:
        from_attributes = True


class SyncMapResponse(BaseModel):
    id: int
    book_pair_id: int
    version: int
    total_sentences: int
    total_chapters: int
    created_at: datetime
    sync_points: List[SyncPointResponse] = []

    class Config:
        from_attributes = True


class SyncMapSummaryResponse(BaseModel):
    """Lightweight sync map response without the full point list."""
    id: int
    book_pair_id: int
    version: int
    total_sentences: int
    total_chapters: int
    created_at: datetime

    class Config:
        from_attributes = True


class SyncPointTextUpdate(BaseModel):
    id: int
    audio_text: str


class SyncMapTextUpdate(BaseModel):
    points: List[SyncPointTextUpdate]


# ============================================================
# Bookmark Schemas
# ============================================================

class BookmarkUpdate(BaseModel):
    source: BookmarkSource
    epub_chapter: Optional[int] = None
    epub_sentence_index: Optional[int] = None
    audio_position_ms: Optional[int] = None


class BookmarkResponse(BaseModel):
    id: int
    user_id: int
    book_pair_id: int
    source: BookmarkSource
    epub_chapter: Optional[int]
    epub_sentence_index: Optional[int]
    audio_position_ms: Optional[int]
    updated_at: datetime
    synced_at: Optional[datetime]

    class Config:
        from_attributes = True


class BookmarkLogResponse(BaseModel):
    id: int
    source: BookmarkSource
    prev_epub_chapter: Optional[int]
    prev_epub_sentence_index: Optional[int]
    prev_audio_position_ms: Optional[int]
    new_epub_chapter: Optional[int]
    new_epub_sentence_index: Optional[int]
    new_audio_position_ms: Optional[int]
    changed_at: datetime

    class Config:
        from_attributes = True


# ============================================================
# User Progress Schemas
# ============================================================

from models.progress import ProgressType

class ProgressUpdate(BaseModel):
    book_pair_id: Optional[int] = None
    epub_cfi: Optional[str] = None
    epub_chapter: Optional[int] = None
    epub_progress_percent: Optional[float] = None
    audio_position_ms: Optional[int] = None
    is_completed: Optional[bool] = None
    device_id: Optional[str] = None

class ProgressResponse(BaseModel):
    id: int
    user_id: int
    media_type: ProgressType
    book_pair_id: Optional[int]
    ebook_id: Optional[int]
    audiobook_id: Optional[int]
    epub_cfi: Optional[str]
    epub_chapter: Optional[int]
    epub_progress_percent: Optional[float]
    audio_position_ms: Optional[int]
    is_completed: bool
    updated_at: datetime
    device_id: Optional[str]

    class Config:
        from_attributes = True


# ============================================================
# Transcription Schemas
# ============================================================

class TranscriptionStatusResponse(BaseModel):
    book_pair_id: int
    status: PairStatus
    progress: Optional[float] = None  # 0.0 to 1.0
    message: Optional[str] = None


# ============================================================
# Library Scan Schemas
# ============================================================

class LibraryScanResponse(BaseModel):
    new_ebooks: int
    new_audiobooks: int
    auto_matched_pairs: int
    message: str

# ============================================================
# Search Schemas
# ============================================================

class SearchResponse(BaseModel):
    query: str
    ebooks: List[EBookResponse]
    audiobooks: List[AudioBookResponse]
    book_pairs: List[BookPairResponse]
