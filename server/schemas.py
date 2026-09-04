"""
Pydantic schemas for API request/response validation.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Generic, Optional, List, TypeVar
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
    # max_length mirrors UserCreate: without it a login attempt could carry an
    # arbitrarily long username straight into the audit row's `details` (Text,
    # uncapped) on every failure — issue #261. No min_length: a too-short
    # username is a wrong username, and 422-ing it would leak which lengths
    # are registrable.
    username: str = Field(..., max_length=50)
    password: str
    # The client's own install id, stored on the session this login opens so a
    # later logout can end just this device (issue #250). Optional: a client
    # that predates it still gets a session, only an unnamed one. Bounded by the
    # column width — a 422 beats a truncated id or a Postgres error mid-login.
    device_id: Optional[str] = Field(default=None, max_length=100)


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
    # Only used when the presented token predates sessions (issue #250): the
    # session it is upgraded to gets named after this device.
    device_id: Optional[str] = Field(default=None, max_length=100)


class LogoutRequest(BaseModel):
    """Body of `POST /api/auth/logout`. Optional in every sense: the endpoint
    accepts no body at all, and then falls back to the session named by the
    access token that authenticated the call (issue #250)."""
    refresh_token: Optional[str] = None


class LogoutResponse(BaseModel):
    message: str
    # "device" if one session ended, "all" if every token the account holds was
    # invalidated. Clients clear their local tokens either way; this is what
    # lets them say which happened.
    scope: str


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
    # The live sync-map version, so a client caching sync points can spot a
    # re-transcription cheaply (issue #55). Null means *unknown* — either the
    # pair has no map, or this endpoint didn't eager-load it (see
    # `BookPair.sync_map_version`) — so a client must not drop its cache on null.
    sync_map_version: Optional[int] = None

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
#
# The write-side `BookmarkUpdate`/`BookmarkResponse` pair is gone with the
# legacy `PUT /sync/bookmark/{pair}` adapter (issue #102) — writes go through
# `PositionUpdate` below. Only the history log still has its own schema.
# ============================================================

class TextMatchRequest(BaseModel):
    epub_text: str
    chapter_hint: int = 0


class TextMatchResponse(BaseModel):
    audio_position_ms: int
    epub_chapter: int
    epub_sentence_index: int
    preview: Optional[str] = None
    # The map version this match was resolved against — the client echoes it
    # as `PositionUpdate.sync_map_version` on the write it derives from this
    # match (issue #116).
    sync_map_version: Optional[int] = None


class AudioToEpubResponse(BaseModel):
    """An audio position re-expressed in EPUB coordinates via the sync map.

    The audio rung of the restore ladder, made executable from the web
    (issue #159): Android resolves it through its cached sync points, the web
    has no local map and asks the server instead.
    """
    epub_chapter: int
    epub_sentence_index: int
    preview: Optional[str] = None
    sync_map_version: Optional[int] = None


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

# `user_progress` is a read-only projection of the canonical record now — there
# is no `ProgressUpdate`, because the legacy `PUT /sync/progress/{type}/{id}`
# adapter is gone (issue #102). Writes go through `PositionUpdate`.

class ProgressResponse(BaseModel):
    id: int
    user_id: int
    media_type: ProgressType
    book_pair_id: Optional[int]
    ebook_id: Optional[int]
    audiobook_id: Optional[int]
    epub_chapter: Optional[int]
    epub_progress_percent: Optional[float]
    audio_position_ms: Optional[int]
    is_completed: bool
    updated_at: datetime
    device_id: Optional[str]
    captured_at: Optional[datetime] = None
    device_name: Optional[str] = None
    # Which format this book opens in next — `bookmarks.source`, read off the
    # canonical record this row projects (issue #215). Not a `user_progress`
    # column: the router lives on the bookmark, and the list endpoint joins it
    # in. None means no canonical record backs this row, which is not the same
    # as "ebook" — clients fall back deliberately rather than being told a
    # claim nobody made.
    source: Optional[BookmarkSource] = None

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
    # Folders of per-track audio the scan refused to import (issue #63);
    # they're listed in Troubleshoot Library.
    multi_file_folders: int = 0
    message: str

# ============================================================
# Search Schemas
# ============================================================

class SearchResponse(BaseModel):
    query: str
    ebooks: List[EBookResponse]
    audiobooks: List[AudioBookResponse]
    book_pairs: List[BookPairResponse]
    # True when any of the three lists hit `library.SEARCH_MAX_RESULTS` and was
    # cut short (issue #208). Additive, and defaulted, so an older client that
    # ignores it keeps working — the API version is unchanged.
    truncated: bool = False

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

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """One page of a list endpoint (issue #48).

    `page` is 1-based and `limit` is the page size that was applied, so a
    client can tell whether there is more (`page * limit < total`) without a
    second request. Same conventions as `GET /api/users/audit-log`.
    """
    items: List[T]
    total: int
    page: int
    limit: int


class NewItemsResponse(BaseModel):
    ebooks: Page[EBookResponse]
    audiobooks: Page[AudioBookResponse]


# ============================================================
# Mixed library browse (issue #120)
# ============================================================

class LibraryItemKind(str, Enum):
    PAIR = "pair"
    EBOOK = "ebook"
    AUDIOBOOK = "audiobook"


class LibraryTab(str, Enum):
    ALL = "all"
    EBOOKS = "ebooks"
    AUDIOBOOKS = "audiobooks"
    PAIRED = "paired"
    UNPAIRED = "unpaired"
    NEW = "new"


class LibrarySort(str, Enum):
    TITLE = "title"
    AUTHOR = "author"
    DATE = "date"
    SERIES = "series"
    SIZE = "size"


class SortDir(str, Enum):
    ASC = "asc"
    DESC = "desc"


class LibraryItem(BaseModel):
    """One row of `GET /api/library/items` — a pair, or an ebook/audiobook.

    `kind` names which of the three is the item. A `pair` item carries the
    pair (with both sides nested) and nothing else; an `ebook`/`audiobook`
    item carries that medium plus, when it is paired, the pair it belongs to,
    so the client can show pairing status without a second lookup. The nested
    models are the same ones the per-type list endpoints return.
    """
    kind: LibraryItemKind
    pair: Optional[BookPairResponse] = None
    ebook: Optional[EBookResponse] = None
    audiobook: Optional[AudioBookResponse] = None


class FacetCount(BaseModel):
    name: str
    count: int


class LibraryCounts(BaseModel):
    ebooks: int
    audiobooks: int
    pairs: int
    unpaired: int
    new_ebooks: int
    new_audiobooks: int
    new_pairs: int


class LibraryFacets(BaseModel):
    """`GET /api/library/facets`: filter-pill options scoped to a tab, plus
    the library-wide counts the tab labels show."""
    authors: List[FacetCount]
    series: List[FacetCount]
    counts: LibraryCounts


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
    # The sync-map version `epub_sentence_index` was resolved against (issue
    # #116). A sentence index is a map coordinate; the server records what the
    # client attests to (NULL when it says nothing) and re-anchors a write
    # whose version trails the live map rather than trusting its index.
    sync_map_version: Optional[int] = None
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
    # Which map the stored sentence index is expressed in; NULL = unknown. A
    # client that pulls this and later pushes it back attests to this value.
    sync_map_version: Optional[int] = None
    epub_text_preview: Optional[str] = None
    epub_progress_percent: Optional[float] = None
    audio_position_ms: Optional[int] = None
    is_completed: bool = False
    captured_at: Optional[datetime] = None
    updated_at: datetime
    device_id: Optional[str] = None
    device_name: Optional[str] = None
    hints: List[PositionHintResponse] = []
