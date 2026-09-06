"""
Pydantic schemas for API request/response validation.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Generic, Optional, List, TypeVar
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

# ============================================================
# Password policy (issue #205)
# ============================================================

#: One policy, shared by every schema that accepts a *new* password, so the
#: web form, the Android sheet and the API cannot disagree about what is
#: acceptable — they used to say 6, 8 and 6 respectively.
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128

#: The exact wording the clients mirror (`web/src/lib/passwordPolicy.js`,
#: `android/.../ui/account/PasswordPolicy.kt`). Keep the three in step.
PASSWORD_POLICY_MESSAGE = (
    f"Password must be between {PASSWORD_MIN_LENGTH} and "
    f"{PASSWORD_MAX_LENGTH} characters."
)


def password_field(**kwargs) -> Any:
    """A `str` field carrying the shared password policy.

    Note on the ceiling: bcrypt only hashes the first 72 *bytes* of its input,
    so anything past that is not actually checked at login. The bound here is
    a denial-of-service guard on the hash rather than a security boundary —
    bcrypt's cost is paid on whatever we hand it — and 128 characters is
    deliberately roomy enough for a real passphrase.

    `UserLogin.password` deliberately does **not** use this: an account created
    under the old 6-character floor must still be able to sign in (and then
    change its password), and a 422 on login would lock those users out of the
    only endpoint that could fix them.
    """
    return Field(
        ...,
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_LENGTH,
        description=PASSWORD_POLICY_MESSAGE,
        **kwargs,
    )


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    password: str = password_field()
    # Issue #210. Optional in the schema and required only by the *server's*
    # mode: a client that predates invites still submits a valid body against an
    # `open` server, and one that sends a code to an `open` server is not an
    # error either — the code is simply ignored. Bounded so a megabyte of
    # "code" cannot be pushed through the hash on an unauthenticated route.
    invite_code: Optional[str] = Field(default=None, max_length=200)


class RegistrationResponse(BaseModel):
    """The one answer `POST /api/auth/register` ever gives (issue #210).

    Accepted, duplicate username, duplicate email, missing invite code, wrong
    invite code, spent invite code, pending queue full — all of them return this
    same body with the same 201, because anything else is an oracle for which
    accounts exist. The admin sees the truth in the pending list; the caller
    sees only that a request was received.
    """

    message: str


class RegistrationModeResponse(BaseModel):
    """`GET /api/auth/registration` — unauthenticated, and deliberately one
    field. The login screens need to know whether to show a request form, an
    invite-code box, or nothing at all, and they ask before anyone has a token.
    Nothing else about the server's configuration belongs in a reply a stranger
    can read."""

    mode: str


class InviteResponse(BaseModel):
    """One invite, as the admin list shows it. **No code** — the row does not
    hold one (only its hash), and the plaintext existed for exactly one
    response."""

    id: int
    created_at: datetime
    expires_at: datetime
    used_at: Optional[datetime] = None
    #: "active" | "used" | "expired"
    status: str
    #: Usernames rather than ids: this is a list a human reads.
    created_by: Optional[str] = None
    used_by: Optional[str] = None


class InviteCreateResponse(InviteResponse):
    """The create response, and the only place a code is ever returned."""

    code: str


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
    new_password: str = password_field()


class AccountDelete(BaseModel):
    """Body for `DELETE /api/auth/me` — self-service account deletion (#146).

    The password is re-checked even though the caller already holds a valid
    access token: this is the one authenticated action that cannot be undone,
    and a token left behind on a borrowed device should not be enough to
    destroy the account it was signed into.
    """
    password: str


class UserCreateAdmin(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    password: str = password_field()
    role: str = Field(default="user")


class UserUpdateAdmin(BaseModel):
    role: Optional[str] = None
    is_active: Optional[bool] = None


class UserPasswordReset(BaseModel):
    new_password: str = password_field()


# ============================================================
# Book Schemas
# ============================================================

class MediaResponseBase(BaseModel):
    """The response fields `EBookResponse` and `AudioBookResponse` share.

    Mirrors `models.book.MediaColumnsMixin` — the two responses were the same 25
    fields written out twice (issue #257), so a field added to one and not the
    other silently vanished from half the API. The key *sets* are pinned by
    `tests/test_media_column_parity.py`; only the JSON key *order* changed when
    this base landed (`duration_seconds` now serialises last on audiobooks
    instead of seventh), which no JSON client can observe.
    """

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


class EBookResponse(MediaResponseBase):
    pass


class AudioBookResponse(MediaResponseBase):
    duration_seconds: Optional[int]


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


# ===========================================================================
# Mirrors of hand-built dicts (issue #258)
# ===========================================================================
#
# The routes below used to return bare dicts. Each model here is a one-to-one
# mirror of that dict — same keys, same optionality — so declaring it changes
# nothing on the wire; it only makes the shape visible in `/openapi.json` and
# validated on the way out. `tests/test_response_model_contracts.py` pins the
# key sets by hand, independently of these classes.

from typing import Dict  # noqa: E402
from pydantic import ConfigDict  # noqa: E402


class StrictResponse(BaseModel):
    """Base for a model that mirrors a handler's hand-built dict.

    ``extra="forbid"`` is deliberate. Pydantic's default is to *ignore* extra
    keys, which would make a key the handler adds (or a typo in one it already
    sends) vanish from the response silently — the exact failure these models
    exist to prevent. With ``forbid``, FastAPI's response validation rejects
    the mismatch with a 500 instead, and the route's tests fail before it
    ships. The cost is that dict and model must move together; that is the
    point.
    """

    model_config = ConfigDict(extra="forbid")


class ActionResult(StrictResponse):
    """``{"status": "..."}`` — an action that has nothing else to report."""

    status: str


class MessageResponse(StrictResponse):
    """``{"message": "..."}`` — a human-readable summary and nothing else."""

    message: str


class DeletedCount(StrictResponse):
    deleted: int


# --- troubleshoot -----------------------------------------------------------

class TroubleshootItem(StrictResponse):
    """One ebook/audiobook row in a troubleshoot category (`_item_dict`)."""

    item_type: str
    item_id: int
    title: str
    author: Optional[str]
    filename: Optional[str]
    file_path: str
    format: Optional[str]
    file_size: Optional[int]
    detail: str
    pair_id: Optional[int]


class TroubleshootFolderItem(TroubleshootItem):
    """A multi-file audiobook folder (issue #63): the item shape plus the
    folder's own fields. `filename` is always null here."""

    file_count: int
    extension: str
    imported_track_count: int


class TroubleshootPairItem(StrictResponse):
    """A row keyed by pair rather than by item — failed transcriptions and
    synced pairs that have lost their sync map."""

    pair_id: int
    ebook_id: int
    audiobook_id: int
    title: str
    author: Optional[str]
    detail: str


class TroubleshootFileItem(StrictResponse):
    """A loose file with no library row behind it — an orphaned cover or a
    quarantined ACSM import."""

    filename: str
    file_path: str
    file_size: int
    detail: str


class TroubleshootCategories(StrictResponse):
    """Every category `GET /api/troubleshoot/issues` reports, always present
    (an empty list when clean). Same keys as `CATEGORIES` in
    `web/src/pages/TroubleshootPage.jsx`."""

    missing: List[TroubleshootItem]
    zero_byte: List[TroubleshootItem]
    chapter_encoding_bad: List[TroubleshootItem]
    audio_corrupt: List[TroubleshootItem]
    ebook_drm: List[TroubleshootItem]
    ebook_unreadable: List[TroubleshootItem]
    unsupported_format: List[TroubleshootItem]
    multi_file_audiobook: List[TroubleshootFolderItem]
    sync_map_missing: List[TroubleshootPairItem]
    duplicate: List[TroubleshootItem]
    missing_cover: List[TroubleshootItem]
    orphaned_cover: List[TroubleshootFileItem]
    failed_transcription: List[TroubleshootPairItem]
    failed_acsm: List[TroubleshootFileItem]


class TroubleshootIssues(StrictResponse):
    categories: TroubleshootCategories
    #: One entry per category, `len()` of the matching list.
    counts: Dict[str, int]
    total: int


class LibraryScanProgress(StrictResponse):
    """`services.library_verify` state as `GET /api/troubleshoot/scan/progress`
    reports it. Timestamps are ISO-8601 strings."""

    running: bool
    phase_index: int
    phase_count: int
    phase_label: str
    current: int
    total: int
    started_at: Optional[str]
    finished_at: Optional[str]
    cancel_requested: bool
    last_error: Optional[str]


class RequeueResult(ActionResult):
    pair_id: int


class MultiFileDismissResult(ActionResult):
    id: int


class MultiFileRemoveTracksResult(DeletedCount):
    id: int


class ReplaceFileResult(ActionResult):
    """`status` is always `"replaced"`; `integrity_ok`/`detail` are the
    re-run integrity check on the new file."""

    integrity_ok: bool
    detail: Optional[str]
    item_id: int


class ChapterRepairResult(ActionResult):
    """`status` is `"repaired"` or `"failed"`; `detail` is null on success."""

    detail: Optional[str]
    item_id: int


class ChapterRepairFailure(StrictResponse):
    item_id: int
    title: Optional[str]
    error: Optional[str]


class BulkChapterRepairResult(StrictResponse):
    repaired: int
    failures: List[ChapterRepairFailure]


class SyncMapAuditRow(StrictResponse):
    """One pair's verdict from `services.sync_map_audit` (issue #295)."""

    pair_id: int
    title: Optional[str]
    ebook_id: int
    ebook_path: Optional[str]
    sync_map_version: Optional[int]
    total_sentences: Optional[int]
    stored_epub_hash: Optional[str]
    current_epub_hash: Optional[str]
    #: "match" | "mismatch" | "unknown" | "file_missing"
    hash_status: str
    #: "ok" | "skipped" | "unreadable"
    text_status: str
    sampled: int
    hits: int
    hit_rate: Optional[float]
    has_cached_transcript: bool
    #: "healthy" | "stale" | "unknown"
    status: str
    reason: str
    #: "realign" | "retranscribe" | "restore_file", or null when healthy
    suggested_action: Optional[str]
    realign_path: Optional[str]


class SyncMapAuditResponse(StrictResponse):
    sample_size: int
    checked: int
    flagged: int
    realign_endpoint: str
    pairs: List[SyncMapAuditRow]


# --- library ------------------------------------------------------------------

class VerifyRow(StrictResponse):
    """A library row whose file is gone from disk (`library._verify_row`)."""

    id: int
    title: str
    author: Optional[str]
    filename: str
    file_path: str
    format: Optional[str]


class VerifyFilesResponse(StrictResponse):
    orphaned_ebooks: List[VerifyRow]
    orphaned_audiobooks: List[VerifyRow]
    #: True when either list hit `library.VERIFY_MAX_RESULTS` (issue #208).
    truncated: bool


class CleanupResponse(MessageResponse):
    deleted_ebooks: int
    deleted_audiobooks: int


class CalibreStatusResponse(StrictResponse):
    """`{"available": true, "version": ...}` or `{"available": false,
    "error": ...}` — the route declares `response_model_exclude_none`, so the
    key that does not apply is absent, exactly as the dict was."""

    available: bool
    version: Optional[str] = None
    error: Optional[str] = None
