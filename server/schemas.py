"""
Pydantic schemas for API request/response validation.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, EmailStr, Field, field_validator
from models.book import PairStatus
from models.bookmark import BookmarkSource


def _naive_utc(value: Optional[datetime]) -> Optional[datetime]:
    """
    Conflict-resolution contract (issue #54): normalize an incoming
    `captured_at` to a naive UTC datetime.

    Pydantic parses a 'Z'/offset-suffixed timestamp (e.g. JS
    `Date.toISOString()` or Kotlin `Instant.toString()`) into a
    timezone-aware datetime, but the DB columns are naive `DateTime` and
    SQLAlchemy always reads them back naive. Comparing an aware value
    against a naive one in `_is_stale` raises `TypeError`. Normalizing here
    guarantees whatever is stored and compared is always naive UTC, leaving
    already-naive inputs (legacy clients) untouched.
    """
    if value is not None and value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


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
    role: str
    is_admin: bool
    is_active: bool
    theme: str = "blueprint"
    must_reset_password: bool = False
    created_at: datetime

    class Config:
        from_attributes = True


class UserUpdateRequest(BaseModel):
    theme: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class TokenRefresh(BaseModel):
    refresh_token: str


class MediaTokenResponse(BaseModel):
    token: str
    expires_in: int


class MediaTokenRequest(BaseModel):
    resource_type: str
    resource_id: str


class MediaTokenBatchRequest(BaseModel):
    resources: List[MediaTokenRequest]


class MediaTokenBatchResponse(BaseModel):
    tokens: dict[str, str]
    expires_in: int


class PasswordChange(BaseModel):
    old_password: str
    new_password: str = Field(..., min_length=6)


class UserCreateAdmin(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=6)
    role: str = Field(default="user")


class UserUpdateAdmin(BaseModel):
    role: Optional[str] = None
    is_active: Optional[bool] = None


class UserPasswordReset(BaseModel):
    new_password: str = Field(..., min_length=6)


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
    acknowledged: bool = False

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
    acknowledged: bool = False

    class Config:
        from_attributes = True


class BookPairResponse(BaseModel):
    id: int
    ebook: EBookResponse
    audiobook: AudioBookResponse
    status: PairStatus
    matched_at: Optional[datetime]
    synced_at: Optional[datetime]
    acknowledged: bool = False

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
# Chapter Schemas
# ============================================================

class Chapter(BaseModel):
    id: int
    start_time: float
    end_time: float
    title: str

# ============================================================
# Match Schemas
# ============================================================

class MatchRequest(BaseModel):
    provider: str
    query: str
    author: Optional[str] = None

class MatchResult(BaseModel):
    id: str
    title: str
    author: Optional[str] = None
    publish_year: Optional[int] = None
    publisher: Optional[str] = None
    description: Optional[str] = None
    isbn: Optional[str] = None
    cover_url: Optional[str] = None
    series: Optional[str] = None
    series_index: Optional[float] = None
    genres: Optional[str] = None      # comma-separated, mirrors EBook/AudioBook columns
    tags: Optional[str] = None        # comma-separated
    language: Optional[str] = None
    narrators: Optional[str] = None
    asin: Optional[str] = None
    duration_seconds: Optional[int] = None

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
    confidence: float = 0.0

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
    # Precise client-side EPUB locator/CFI. Persisted and echoed back so the
    # client can resume at the exact reading position.
    epub_locator: Optional[str] = None
    # Audio position the locator was captured at; travels with the locator.
    locator_audio_ms: Optional[int] = None
    # When False (default), the bookmark is updated but no BookmarkLog row is
    # appended. Clients set this to True only on meaningful session boundaries:
    # pause, stop (track ended, player closed, cast session ends), or every
    # 30 minutes of continuous playback. Keeps the history tab scannable.
    append_to_log: bool = False
    # Conflict-resolution contract (issue #54): wall-clock time the client
    # captured this position. Omitted by legacy clients, which preserves the
    # old last-write-wins behavior (no staleness check is possible without it).
    captured_at: Optional[datetime] = None
    device_id: Optional[str] = None
    device_name: Optional[str] = None

    @field_validator("captured_at")
    @classmethod
    def _normalize_captured_at(cls, value: Optional[datetime]) -> Optional[datetime]:
        return _naive_utc(value)


class TextMatchRequest(BaseModel):
    epub_text: str
    chapter_hint: int = 0


class TextMatchResponse(BaseModel):
    audio_position_ms: int
    epub_chapter: int
    epub_sentence_index: int
    preview: Optional[str] = None


class BookmarkResponse(BaseModel):
    id: int
    user_id: int
    book_pair_id: int
    source: BookmarkSource
    epub_chapter: Optional[int]
    epub_sentence_index: Optional[int]
    audio_position_ms: Optional[int]
    epub_locator: Optional[str] = None
    locator_audio_ms: Optional[int] = None
    epub_text_preview: Optional[str] = None
    updated_at: datetime
    synced_at: Optional[datetime]
    captured_at: Optional[datetime] = None
    device_id: Optional[str] = None
    device_name: Optional[str] = None

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
    device_id: Optional[str] = None
    device_name: Optional[str] = None
    captured_at: Optional[datetime] = None

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
    # Conflict-resolution contract (issue #54): wall-clock time the client
    # captured this position. Omitted by legacy clients, which preserves the
    # old last-write-wins behavior (no staleness check is possible without it).
    captured_at: Optional[datetime] = None
    device_name: Optional[str] = None

    @field_validator("captured_at")
    @classmethod
    def _normalize_captured_at(cls, value: Optional[datetime]) -> Optional[datetime]:
        return _naive_utc(value)

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
    captured_at: Optional[datetime] = None
    device_name: Optional[str] = None

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


class QueueItemResponse(BaseModel):
    id: int
    book_pair_id: int
    book_title: Optional[str] = None
    status: str
    priority: int
    position: int = 0
    progress: Optional[float] = None
    message: Optional[str] = None
    error_message: Optional[str] = None
    retry_count: int = 0
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    # Off-hours window (issue #106): force_run marks a "Run now" override,
    # paused_at marks an item holding a resumable checkpoint on the worker.
    force_run: bool = False
    paused_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class OffHoursStatusResponse(BaseModel):
    """Current state of the off-hours transcription window."""

    enabled: bool
    open: bool
    start: str
    end: str
    timezone: str
    # Null when the window is disabled.
    opens_at: Optional[datetime] = None
    closes_at: Optional[datetime] = None


class QueueAddRequest(BaseModel):
    pair_ids: List[int]


class QueuePriorityUpdate(BaseModel):
    priority: int


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

# ============================================================
# Metadata Cleanup Schemas
# ============================================================

class DiscrepantField(BaseModel):
    field: str
    ebook_value: Optional[str]
    audiobook_value: Optional[str]

class MetadataDiscrepancy(BaseModel):
    pair_id: int
    ebook_id: int
    audiobook_id: int
    title: str
    discrepancies: List[DiscrepantField]

class ResolveDiscrepancyRequest(BaseModel):
    # Dictionaries mapping field_name to the resolved value
    ebook_updates: dict[str, Optional[str]]
    audiobook_updates: dict[str, Optional[str]]

class IgnoreDiscrepancyRequest(BaseModel):
    fields: List[str]


# ============================================================
# New Items / New Pairs Inbox Schemas
# ============================================================

class NewItemsResponse(BaseModel):
    ebooks: List[EBookResponse]
    audiobooks: List[AudioBookResponse]


class AcknowledgeItemsRequest(BaseModel):
    ebook_ids: List[int] = []
    audiobook_ids: List[int] = []


class AcknowledgePairsRequest(BaseModel):
    pair_ids: List[int]


# ============================================================
# Canonical Position Schemas
# ============================================================

from models.bookmark import HintKind  # noqa: E402


class PositionScope(str, Enum):
    """What a position belongs to. Standalone media had no canonical position
    row at all before; it lived only in user_progress."""
    PAIR = "pair"
    EBOOK = "ebook"
    AUDIOBOOK = "audiobook"


class PositionHintPayload(BaseModel):
    """A reader-specific precise position, offered with the write that set the
    anchor. The server tags it with the resulting anchor revision, which is how
    staleness is judged later — hints are never deleted."""
    kind: HintKind
    value: str
    audio_position_ms: Optional[int] = None


class PositionHintResponse(BaseModel):
    kind: HintKind
    device_id: str
    value: str
    anchor_revision: int
    audio_position_ms: Optional[int] = None
    # True when captured at the position's live anchor. A false hint is stale,
    # not useless: its device makes it current again by re-capturing.
    current: bool


class PositionUpdate(BaseModel):
    """One write carrying the whole position.

    Every field is optional and omission means "leave alone" — a write that
    carries no anchor never clears one.

    `source` follows the same rule: only a foreground, user-initiated write
    claims which format the clients open next. A background write (service
    teardown, Android Auto heartbeat) omits it to move the position without
    re-stamping the format (issue: background writes re-claiming source).
    """
    source: Optional[BookmarkSource] = None
    # Spine index: the axis both readers position by.
    epub_chapter: Optional[int] = None
    epub_sentence_index: Optional[int] = None
    epub_text_preview: Optional[str] = None
    epub_progress_percent: Optional[float] = None
    audio_position_ms: Optional[int] = None
    is_completed: Optional[bool] = None
    hint: Optional[PositionHintPayload] = None
    append_to_log: bool = False
    captured_at: Optional[datetime] = None
    device_id: Optional[str] = None
    device_name: Optional[str] = None

    @field_validator("captured_at")
    @classmethod
    def _normalize_captured_at(cls, value: Optional[datetime]) -> Optional[datetime]:
        return _naive_utc(value)


class PositionResponse(BaseModel):
    scope: PositionScope
    book_pair_id: Optional[int] = None
    ebook_id: Optional[int] = None
    audiobook_id: Optional[int] = None
    source: BookmarkSource
    anchor_revision: int
    epub_chapter: Optional[int] = None
    epub_sentence_index: Optional[int] = None
    epub_text_preview: Optional[str] = None
    epub_progress_percent: Optional[float] = None
    audio_position_ms: Optional[int] = None
    is_completed: bool = False
    captured_at: Optional[datetime] = None
    updated_at: datetime
    device_id: Optional[str] = None
    device_name: Optional[str] = None
    hints: List[PositionHintResponse] = []
