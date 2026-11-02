"""
Book models: EBook, AudioBook, and BookPair (the link between them).
"""

import enum
from datetime import datetime
from typing import Optional
from sqlalchemy import (
    String, Text, DateTime, Integer, BigInteger, Boolean, Enum, ForeignKey,
    Float, Index, JSON, inspect,
)
from sqlalchemy import event, false
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base
from utils import utcnow

# Portable JSON everywhere, but real JSONB on Postgres so fresh databases built
# from these models match the production schema (which the old boot-time ALTER
# block created as JSONB). SQLite (tests) still gets plain JSON.
JSON_OR_JSONB = JSON().with_variant(JSONB(), "postgresql")


class PairStatus(str, enum.Enum):
    """Status of an ebook/audiobook pairing."""
    UNMATCHED = "unmatched"
    AUTO_MATCHED = "auto_matched"
    MANUAL_MATCHED = "manual_matched"
    TRANSCRIBING = "transcribing"
    SYNCED = "synced"
    ERROR = "error"


class MediaColumnsMixin:
    """The columns `ebooks` and `audiobooks` share, declared once (issue #257).

    The two tables are the same table twice — every column below was written out
    verbatim in both classes, so every metadata feature had to be added twice and
    nothing caught a copy that missed one. Mixing this in is **schema-neutral**:
    SQLAlchemy copies each `mapped_column` onto the concrete class, producing the
    same names, types, nullability and defaults as before. `tests/
    test_media_column_parity.py` is the gate; `alembic check` is the proof.

    Deliberately *not* here, because they differ per media type:

    * ``format`` — defaults to ``epub`` on ebooks and ``mp3`` on audiobooks;
    * ``__table_args__`` — the indexes carry the table name;
    * ``pairs`` — the relationship's ``back_populates`` differs;
    * ``duration_seconds`` — audiobooks only.
    """

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    author: Mapped[str] = mapped_column(String(500), nullable=True)
    filename: Mapped[str] = mapped_column(String(1000), nullable=False)
    file_path: Mapped[str] = mapped_column(String(2000), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=True)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    # Series are not columns: a book can belong to several, so they live in the
    # per-table child rows `series_list` (issue #905). This flag is the one
    # thing that stays here — it records that a user emptied the list, so a
    # rescan or an Audiobookshelf enrich does not put it back. It replaces the
    # old `series = ''` sentinel.
    # `default=False` gives a freshly flushed row a known value without an
    # expired-attribute load (which would raise MissingGreenlet in an async
    # handler); `server_default` matches the migration and lets raw inserts
    # that do not name the column work, as for `acknowledged`.
    series_cleared: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false(),
    )
    metadata_source: Mapped[str] = mapped_column(String(50), nullable=True)  # 'embedded', 'pattern', 'filename'
    metadata_pattern: Mapped[str] = mapped_column(String(500), nullable=True)  # the pattern that matched, if any

    # Extended metadata fields
    description: Mapped[str] = mapped_column(Text, nullable=True)
    publisher: Mapped[str] = mapped_column(String(500), nullable=True)
    publish_year: Mapped[int] = mapped_column(Integer, nullable=True)
    language: Mapped[str] = mapped_column(String(50), nullable=True)
    genres: Mapped[str] = mapped_column(String(1000), nullable=True)  # comma-separated
    tags: Mapped[str] = mapped_column(String(1000), nullable=True)  # comma-separated
    isbn: Mapped[str] = mapped_column(String(100), nullable=True)
    asin: Mapped[str] = mapped_column(String(100), nullable=True)
    narrators: Mapped[str] = mapped_column(String(500), nullable=True)
    is_explicit: Mapped[bool] = mapped_column(Boolean, nullable=True, default=False)
    is_abridged: Mapped[bool] = mapped_column(Boolean, nullable=True, default=False)
    cover_path: Mapped[str] = mapped_column(String(2000), nullable=True)

    # Import provenance — set when this row was created via an automated import source.
    import_source: Mapped[str] = mapped_column(String(50), nullable=True)
    external_id: Mapped[str] = mapped_column(String(200), nullable=True)

    # New-items inbox: cleared once user acknowledges or pairs this item
    acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Hashes of the *other* media type this row must not be auto-paired with
    # (set on manual unpair).
    # Rows of the *other* media type this row must not be auto-paired with
    # (set on manual unpair). Two keys, either of which blocks the pair: the
    # row ids — always available, so a hash-less legacy row still remembers
    # the unpair (issue #253) — and the file hashes below, which survive a
    # delete-and-reingest at a new id and are what `POST /rehash` remaps.
    auto_pair_excluded_ids: Mapped[list] = mapped_column(JSON_OR_JSONB, nullable=False, default=list)
    auto_pair_excluded_hashes: Mapped[list] = mapped_column(JSON_OR_JSONB, nullable=False, default=list)


class _SeriesEntryColumns:
    """The columns `ebook_series` and `audiobook_series` share (issue #905).

    One row per series a book belongs to. `position` is the display order and
    entry 0 is the book's *primary* series; `(owner, position)` is unique so the
    order is always well defined. The index column is named `series_index` in the
    table (the name the old scalar column had) but `index` on the Python side,
    which is what the API calls it.
    """

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    index: Mapped[Optional[float]] = mapped_column("series_index", Float, nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class EBook(MediaColumnsMixin, Base):
    """An uploaded or discovered EPUB/ebook file.

    Everything but the columns below comes from `MediaColumnsMixin`.
    """

    __tablename__ = "ebooks"

    format: Mapped[str] = mapped_column(String(10), default="epub")  # epub, pdf, etc.

    # Issue #730: pages in the printed edition, for the reader's "actual pages"
    # when the EPUB embeds no page list. Set by an editor or from a Google Books
    # match the editor accepted; NULL when unknown.
    print_page_count: Mapped[int] = mapped_column(Integer, nullable=True)
    # Issue #739: when the Google Books fill job last looked this ebook up,
    # found or not, so a later run skips it instead of spending the daily quota
    # on the same miss again. NULL means never tried.
    print_pages_looked_up_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    # `file_path` is the identity key: the scan, the ACSM/convert path and the
    # importers all look a row up by it with `.scalar_one_or_none()`. Every
    # insert is check-then-insert on one worker, so nothing *produces* a
    # duplicate today — but one arriving any other way (a manual insert, a
    # restore from an older dump, a second worker later) makes every one of
    # those lookups raise `MultipleResultsFound`, and the whole library scan
    # 500s until a row is deleted by hand. That is issue #64 again, in a
    # different table; here it is unrepresentable instead (issue #256).
    #
    # `file_hash` is the auto-pair and duplicate-detection key, read per file
    # per scan; indexed for the lookup, not constrained — two identical files at
    # different paths are legitimate.
    __table_args__ = (
        Index("ux_ebooks_file_path", "file_path", unique=True),
        Index("ix_ebooks_file_hash", "file_hash"),
    )

    # Relationships
    pairs = relationship("BookPair", back_populates="ebook", cascade="all, delete-orphan")
    # selectin so every list endpoint serialises `series_list` without an async
    # lazy-load error.
    series_list = relationship(
        "EBookSeries", order_by="EBookSeries.position",
        cascade="all, delete-orphan", lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<EBook(id={self.id}, title='{self.title}')>"


class AudioBook(MediaColumnsMixin, Base):
    """An uploaded or discovered audiobook file.

    Everything but the columns below comes from `MediaColumnsMixin`.
    `duration_seconds` is the only column this table has and `ebooks` does not.
    """

    __tablename__ = "audiobooks"

    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=True)
    format: Mapped[str] = mapped_column(String(10), default="mp3")

    # Same reasoning as `EBook` above (issue #256): `file_path` is the identity
    # key every scan lookup assumes is unique, `file_hash` is a hot read.
    __table_args__ = (
        Index("ux_audiobooks_file_path", "file_path", unique=True),
        Index("ix_audiobooks_file_hash", "file_hash"),
    )

    # Relationships
    pairs = relationship("BookPair", back_populates="audiobook", cascade="all, delete-orphan")
    series_list = relationship(
        "AudioBookSeries", order_by="AudioBookSeries.position",
        cascade="all, delete-orphan", lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<AudioBook(id={self.id}, title='{self.title}')>"


def _start_without_series(target, args, kwargs):
    """A new row has no series, and says so.

    Without this a freshly inserted row carries an *unloaded* `series_list`, and
    reading it afterwards (the matcher scoring two rows it has just added, say)
    is an implicit lazy load, which an async session refuses (`MissingGreenlet`).
    Rows read back from the database never come through here; `lazy="selectin"`
    loads theirs with the query.
    """
    kwargs.setdefault("series_list", [])


event.listen(EBook, "init", _start_without_series)
event.listen(AudioBook, "init", _start_without_series)


class EBookSeries(_SeriesEntryColumns, Base):
    """One series an ebook belongs to (issue #905)."""

    __tablename__ = "ebook_series"

    ebook_id: Mapped[int] = mapped_column(
        ForeignKey("ebooks.id", ondelete="CASCADE"), nullable=False
    )

    __table_args__ = (
        Index("ux_ebook_series_position", "ebook_id", "position", unique=True),
        Index("ix_ebook_series_name", "name"),
    )

    def __repr__(self) -> str:
        return f"<EBookSeries(ebook_id={self.ebook_id}, name='{self.name}', index={self.index})>"


class AudioBookSeries(_SeriesEntryColumns, Base):
    """One series an audiobook belongs to (issue #905)."""

    __tablename__ = "audiobook_series"

    audiobook_id: Mapped[int] = mapped_column(
        ForeignKey("audiobooks.id", ondelete="CASCADE"), nullable=False
    )

    __table_args__ = (
        Index("ux_audiobook_series_position", "audiobook_id", "position", unique=True),
        Index("ix_audiobook_series_name", "name"),
    )

    def __repr__(self) -> str:
        return f"<AudioBookSeries(audiobook_id={self.audiobook_id}, name='{self.name}', index={self.index})>"


class BookPair(Base):
    """
    A matched pair linking an ebook to its audiobook.
    This is the central entity that sync maps and bookmarks reference.
    """

    __tablename__ = "book_pairs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ebook_id: Mapped[int] = mapped_column(ForeignKey("ebooks.id"), nullable=False)
    audiobook_id: Mapped[int] = mapped_column(ForeignKey("audiobooks.id"), nullable=False)
    status: Mapped[PairStatus] = mapped_column(
        Enum(PairStatus), default=PairStatus.UNMATCHED, nullable=False
    )
    matched_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    ignored_fields: Mapped[list] = mapped_column(JSON_OR_JSONB, nullable=False, default=list)

    # New-pairs inbox: cleared once user resolves/skips all discrepancies or manually acknowledges
    acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # A pair is strictly one-to-one (issue #691): an ebook may be in at most one
    # `BookPair`, and an audiobook may be in at most one. Before this, the only
    # uniqueness was on the *combination* of the two ids, so the same ebook
    # could be paired to two different audiobooks (or vice versa) without
    # either the DB or `create_pair` objecting — which meant the same book got
    # queued for transcription twice, and a reader's position could silently
    # split across the two pairs (`user_progress`/`bookmarks` key on
    # `book_pair_id`).
    #
    # These are plain unique `Index`es rather than a table-level
    # `UniqueConstraint` so they stay individually droppable on SQLite (a
    # `UniqueConstraint` is baked into `CREATE TABLE` there and needs a full
    # table rebuild to remove — see `tests.factories.suspend_book_pair_uniqueness`).
    # They also serve the paired/unpaired filter's `EXISTS (SELECT 1 FROM
    # book_pairs WHERE ebook_id = ebooks.id)` per-row check on every library
    # page, same as the plain indexes they replace.
    #
    # `uq_book_pairs_pair` (unique on the *combination*) is gone: unique
    # `ebook_id` alone already implies the combination can never repeat, so
    # keeping both was redundant (migration 0025_book_pairs_one_to_one).
    __table_args__ = (
        Index("ux_book_pairs_ebook_id", "ebook_id", unique=True),
        Index("ux_book_pairs_audiobook_id", "audiobook_id", unique=True),
    )

    # Relationships
    ebook = relationship("EBook", back_populates="pairs")
    audiobook = relationship("AudioBook", back_populates="pairs")
    sync_map = relationship("SyncMap", back_populates="book_pair", uselist=False, cascade="all, delete-orphan")
    bookmarks = relationship("Bookmark", back_populates="book_pair", cascade="all, delete-orphan")

    @property
    def sync_map_version(self) -> "int | None":
        """The live `sync_maps.version`, for clients that cache sync points.

        Re-transcription rebuilds the map under a new version (issue #55), and a
        client holding stale points converts positions with timestamps that no
        longer exist. Surfacing the version on the pair listing lets them notice
        without downloading the whole map.

        Returns None when the relationship isn't loaded — an async lazy-load
        raises, and several endpoints (`POST /pairs`, for one) never eager-load
        it. Callers must therefore read null as *unknown*, not as "no map", and
        leave their cache alone.
        """
        if "sync_map" in inspect(self).unloaded:
            return None
        return self.sync_map.version if self.sync_map else None

    def __repr__(self) -> str:
        return f"<BookPair(id={self.id}, status='{self.status}')>"
