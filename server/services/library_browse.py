"""
Library browse: the paginated lists, the global search, the mixed `/items`
list and its facets, driven without HTTP.

Split out of `routers/library.py` (issue #255). The router keeps every
endpoint -- path, method, auth, `response_model` -- and its FastAPI `Query`
declarations (`_page_param`, `_limit_param`, `_q_param`); everything that
builds or runs a statement lives here:

* `_search_clause` / `_like_term`: the case-insensitive substring match on
  title/author/series shared by the lists and `/search`, with LIKE's own
  wildcards neutralised (issue #208);
* `_paginate` / `_list_media` / `_pairs_base` / `_library_order`: the
  `{items, total, page, limit}` envelope every list returns (issue #48);
* `search_impl`: the three capped queries behind `GET /search`;
* `_browse_subquery` and its arms, `_browse_order`, `_hydrate_items`,
  `list_items_impl` and `facets_impl`: the UNION ALL that lets the database
  filter, sort, count and page the mixed Library page in one pass
  (issue #120).

The names keep their router-era underscores because the router re-exports
them under the same names and the existing tests reach them there.
"""

from typing import Dict, List, Optional

from sqlalchemy import select, or_, func, literal, null, cast, Integer, String, exists, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from models.book import EBook, AudioBook, BookPair
from schemas import (
    EBookResponse, AudioBookResponse, BookPairResponse, SearchResponse, Page,
    LibraryItem, LibraryItemKind, LibraryTab, LibrarySort, SortDir,
    LibraryFacets, LibraryCounts, FacetCount,
)

# `page`/`limit` follow GET /api/users/audit-log: 1-based page, limit 1..500,
# default 100 (issue #48). The router's `Query` declarations read these.

PAGE_DEFAULT_LIMIT = 100
PAGE_MAX_LIMIT = 500

# Hard cap on `/api/library/search`, which is not paginated (issue #208). Three
# unbounded queries, and — before the escaping below — `q=%` returned the whole
# library three times over in one request. 200 is well above any result a person
# scans by eye and well below anything that hurts.
SEARCH_MAX_RESULTS = 200

# LIKE's own wildcards. Someone typing `%` into a search box means the character,
# not "everything"; `_` means the character, not "any character". Escaped with a
# backslash, declared to the DB with `escape="\\"` so the same term behaves
# identically on SQLite and Postgres.
_LIKE_ESCAPE = "\\"


def _like_term(q: str) -> str:
    """`q` as a LIKE substring pattern with its wildcards neutralised.

    The backslash is escaped first, or escaping the wildcards afterwards would
    re-escape the escapes.
    """
    escaped = q.lower()
    for ch in (_LIKE_ESCAPE, "%", "_"):
        escaped = escaped.replace(ch, _LIKE_ESCAPE + ch)
    return f"%{escaped}%"


def _search_clause(model, q: Optional[str]):
    """`WHERE lower(title|author|series) LIKE %q%` for one model, or None."""
    if not q:
        return None
    term = _like_term(q)
    return or_(
        func.lower(model.title).like(term, escape=_LIKE_ESCAPE),
        func.lower(model.author).like(term, escape=_LIKE_ESCAPE),
        func.lower(model.series).like(term, escape=_LIKE_ESCAPE),
    )


def _library_order(model):
    """The library's browse order: author → series → series index → title, then id."""
    return (
        model.author.nulls_last(), model.series.nulls_last(),
        model.series_index.nulls_last(), model.title, model.id,
    )


async def _paginate(db: AsyncSession, query, count_from, page: int, limit: int) -> Page:
    """Run `query` for one page and a matching count.

    `count_from` is the FROM/JOIN/WHERE part of the same statement with no
    ORDER BY or eager-load options, so the total reflects exactly the filtered
    set. Returns a `Page` whose `items` are ORM rows — the endpoint's
    `response_model` serialises them.
    """
    total = (await db.execute(
        select(func.count()).select_from(count_from.subquery())
    )).scalar_one()
    rows = (await db.execute(
        query.offset((page - 1) * limit).limit(limit)
    )).scalars().all()
    return Page(items=rows, total=total, page=page, limit=limit)


async def _list_media(db, model, *, page, limit, q, where=None, order=None):
    """Shared body of the ebook/audiobook list endpoints."""
    base = select(model)
    if where is not None:
        base = base.where(where)
    clause = _search_clause(model, q)
    if clause is not None:
        base = base.where(clause)
    ordered = base.order_by(*(order if order is not None else _library_order(model)))
    return await _paginate(db, ordered, base, page, limit)


async def search_impl(db: AsyncSession, q: str) -> SearchResponse:
    """Body of `GET /search`: the three capped queries and the `truncated` flag."""
    # Ask for one more than the cap: if it comes back, there was more to find.
    probe = SEARCH_MAX_RESULTS + 1

    ebook_query = select(EBook).where(_search_clause(EBook, q)).limit(probe)
    ebooks = (await db.execute(ebook_query)).scalars().all()

    audiobook_query = select(AudioBook).where(_search_clause(AudioBook, q)).limit(probe)
    audiobooks = (await db.execute(audiobook_query)).scalars().all()

    # Pairs match on either side.
    pair_query = (
        select(BookPair)
        .options(selectinload(BookPair.ebook), selectinload(BookPair.audiobook))
        .join(BookPair.ebook)
        .join(BookPair.audiobook)
        .where(or_(_search_clause(EBook, q), _search_clause(AudioBook, q)))
        .limit(probe)
    )
    pairs = (await db.execute(pair_query)).scalars().all()

    truncated = any(len(rows) > SEARCH_MAX_RESULTS
                    for rows in (ebooks, audiobooks, pairs))

    return SearchResponse(
        query=q,
        ebooks=ebooks[:SEARCH_MAX_RESULTS],
        audiobooks=audiobooks[:SEARCH_MAX_RESULTS],
        book_pairs=pairs[:SEARCH_MAX_RESULTS],
        truncated=truncated,
    )


def _pairs_base(q: Optional[str], where=None):
    """FROM/JOIN/WHERE for a pairs listing; `q` matches either side of the pair."""
    base = select(BookPair).join(BookPair.ebook).join(BookPair.audiobook)
    if where is not None:
        base = base.where(where)
    if q:
        base = base.where(or_(_search_clause(EBook, q), _search_clause(AudioBook, q)))
    return base


_PAIR_LOADS = (
    # `sync_map` is eager-loaded purely so `sync_map_version` can be
    # reported here — this is the listing Android's `refreshPairs` polls,
    # and it is how a client learns its cached sync points went stale.
    selectinload(BookPair.ebook), selectinload(BookPair.audiobook),
    selectinload(BookPair.sync_map),
)


# ---------------------------------------------------------------------------
# Mixed library browse + facets (issue #120)
#
# The web LibraryPage renders one list: each pair once (ebook-primary for its
# display fields) plus every unpaired ebook and audiobook, filtered by tab /
# search / author / series, sorted, and paged. It used to download the whole
# library and do all of that in memory. Here the three shapes are normalised
# into one UNION ALL subquery so the database can filter, sort, count and page
# it in a single pass; the page's rows are then hydrated into the ordinary
# response models.
# ---------------------------------------------------------------------------

def _int_null():
    # A typed NULL, so the union's column types agree on Postgres.
    return cast(null(), Integer)


def _paired_ebook(): return exists(select(BookPair.id).where(BookPair.ebook_id == EBook.id))
def _paired_audiobook(): return exists(select(BookPair.id).where(BookPair.audiobook_id == AudioBook.id))


def _pair_arm(where=None):
    """Pairs, ebook-primary: title/author/series/... come from the ebook and
    fall back to the audiobook — the same rule the web used in memory."""
    q = select(
        literal("pair").label("kind"),
        BookPair.id.label("item_id"),
        BookPair.id.label("pair_id"),
        EBook.id.label("ebook_id"),
        AudioBook.id.label("audiobook_id"),
        func.coalesce(EBook.title, AudioBook.title).label("title"),
        func.coalesce(EBook.author, AudioBook.author).label("author"),
        func.coalesce(EBook.series, AudioBook.series).label("series"),
        func.coalesce(EBook.series_index, AudioBook.series_index).label("series_index"),
        func.coalesce(EBook.uploaded_at, AudioBook.uploaded_at).label("uploaded_at"),
        func.coalesce(EBook.file_size, AudioBook.file_size).label("file_size"),
        BookPair.acknowledged.label("acknowledged"),
        # The audiobook side, so `q` matches either half of a pair — the
        # display columns above are ebook-primary.
        AudioBook.title.label("alt_title"),
        AudioBook.author.label("alt_author"),
        AudioBook.series.label("alt_series"),
    ).select_from(BookPair).join(EBook, BookPair.ebook_id == EBook.id).join(
        AudioBook, BookPair.audiobook_id == AudioBook.id)
    return q.where(where) if where is not None else q


def _media_arm(model, kind: str, where=None):
    ebook_id = model.id if model is EBook else _int_null()
    audiobook_id = model.id if model is AudioBook else _int_null()
    q = select(
        literal(kind).label("kind"),
        model.id.label("item_id"),
        _int_null().label("pair_id"),
        ebook_id.label("ebook_id"),
        audiobook_id.label("audiobook_id"),
        model.title.label("title"),
        model.author.label("author"),
        model.series.label("series"),
        model.series_index.label("series_index"),
        model.uploaded_at.label("uploaded_at"),
        model.file_size.label("file_size"),
        model.acknowledged.label("acknowledged"),
        cast(null(), String).label("alt_title"),
        cast(null(), String).label("alt_author"),
        cast(null(), String).label("alt_series"),
    )
    return q.where(where) if where is not None else q


def _browse_arms(tab: LibraryTab, kind: Optional[LibraryItemKind]):
    """Which of the three shapes a tab (and its optional sub-kind) shows.

    Mirrors the web's tab semantics exactly: `all` is pairs + unpaired media;
    `ebooks`/`audiobooks` are every ebook/audiobook (paired ones carry their
    pair); `new` is unacknowledged media (or, with kind=pair, unacknowledged
    pairs).
    """
    want = lambda k: kind is None or kind == k  # noqa: E731
    arms = []
    if tab == LibraryTab.ALL:
        arms = [_pair_arm(), _media_arm(EBook, "ebook", ~_paired_ebook()),
                _media_arm(AudioBook, "audiobook", ~_paired_audiobook())]
    elif tab == LibraryTab.EBOOKS:
        arms = [_media_arm(EBook, "ebook")]
    elif tab == LibraryTab.AUDIOBOOKS:
        arms = [_media_arm(AudioBook, "audiobook")]
    elif tab == LibraryTab.PAIRED:
        arms = [_pair_arm()]
    elif tab == LibraryTab.UNPAIRED:
        if want(LibraryItemKind.EBOOK):
            arms.append(_media_arm(EBook, "ebook", ~_paired_ebook()))
        if want(LibraryItemKind.AUDIOBOOK):
            arms.append(_media_arm(AudioBook, "audiobook", ~_paired_audiobook()))
    elif tab == LibraryTab.NEW:
        if kind == LibraryItemKind.PAIR:
            arms = [_pair_arm(BookPair.acknowledged == False)]  # noqa: E712
        else:
            if want(LibraryItemKind.EBOOK):
                arms.append(_media_arm(EBook, "ebook", EBook.acknowledged == False))  # noqa: E712
            if want(LibraryItemKind.AUDIOBOOK):
                arms.append(_media_arm(AudioBook, "audiobook", AudioBook.acknowledged == False))  # noqa: E712
    return arms


def _browse_subquery(tab: LibraryTab, kind: Optional[LibraryItemKind]):
    arms = _browse_arms(tab, kind)
    if not arms:
        # An impossible tab/kind combination (e.g. paired + kind=ebook):
        # an empty set, expressed as a query so the callers stay uniform.
        return _media_arm(EBook, "ebook", literal(False)).subquery("browse")
    return union_all(*arms).subquery("browse")


def _browse_order(u, sort: LibrarySort, direction: SortDir):
    """ORDER BY for the browse list; every key ends in (kind, item_id) so pages
    are disjoint and stable, like every other list here."""
    desc = direction == SortDir.DESC

    def key(col):
        return col.desc().nulls_last() if desc else col.asc().nulls_last()

    if sort == LibrarySort.AUTHOR:
        keys = [key(u.c.author), key(u.c.series), key(u.c.series_index), key(u.c.title)]
    elif sort == LibrarySort.SERIES:
        keys = [key(u.c.series), key(u.c.series_index), key(u.c.title)]
    elif sort == LibrarySort.DATE:
        keys = [key(u.c.uploaded_at), key(u.c.title)]
    elif sort == LibrarySort.SIZE:
        keys = [key(u.c.file_size), key(u.c.title)]
    else:
        keys = [key(u.c.title)]
    return keys + [u.c.kind, u.c.item_id]


async def _hydrate_items(db: AsyncSession, rows) -> List[LibraryItem]:
    """Turn the page's union rows into LibraryItems, in the same order."""
    pair_ids = [r.pair_id for r in rows if r.kind == "pair"]
    ebook_ids = [r.ebook_id for r in rows if r.kind == "ebook"]
    audiobook_ids = [r.audiobook_id for r in rows if r.kind == "audiobook"]

    pairs: Dict[int, BookPair] = {}
    if pair_ids:
        for p in (await db.execute(
            select(BookPair).options(*_PAIR_LOADS).where(BookPair.id.in_(pair_ids))
        )).scalars():
            pairs[p.id] = p

    ebooks: Dict[int, EBook] = {}
    pair_by_ebook: Dict[int, BookPair] = {}
    if ebook_ids:
        for e in (await db.execute(select(EBook).where(EBook.id.in_(ebook_ids)))).scalars():
            ebooks[e.id] = e
        for p in (await db.execute(
            select(BookPair).options(*_PAIR_LOADS).where(BookPair.ebook_id.in_(ebook_ids))
        )).scalars():
            pair_by_ebook[p.ebook_id] = p

    audiobooks: Dict[int, AudioBook] = {}
    pair_by_audiobook: Dict[int, BookPair] = {}
    if audiobook_ids:
        for a in (await db.execute(select(AudioBook).where(AudioBook.id.in_(audiobook_ids)))).scalars():
            audiobooks[a.id] = a
        for p in (await db.execute(
            select(BookPair).options(*_PAIR_LOADS).where(BookPair.audiobook_id.in_(audiobook_ids))
        )).scalars():
            pair_by_audiobook[p.audiobook_id] = p

    def pair_model(p):
        return BookPairResponse.model_validate(p) if p is not None else None

    items: List[LibraryItem] = []
    for r in rows:
        if r.kind == "pair":
            p = pairs.get(r.pair_id)
            if p is None:
                continue  # deleted between the count and the hydrate
            items.append(LibraryItem(kind=LibraryItemKind.PAIR, pair=pair_model(p)))
        elif r.kind == "ebook":
            e = ebooks.get(r.ebook_id)
            if e is None:
                continue
            items.append(LibraryItem(
                kind=LibraryItemKind.EBOOK, ebook=EBookResponse.model_validate(e),
                pair=pair_model(pair_by_ebook.get(e.id)),
            ))
        else:
            a = audiobooks.get(r.audiobook_id)
            if a is None:
                continue
            items.append(LibraryItem(
                kind=LibraryItemKind.AUDIOBOOK, audiobook=AudioBookResponse.model_validate(a),
                pair=pair_model(pair_by_audiobook.get(a.id)),
            ))
    return items


async def list_items_impl(
    db: AsyncSession,
    *,
    tab: LibraryTab,
    kind: Optional[LibraryItemKind],
    q: Optional[str],
    author: Optional[str],
    series: Optional[str],
    sort: LibrarySort,
    direction: SortDir,
    page: int,
    limit: int,
) -> Page:
    """Body of `GET /items`: filter, count, order and page the mixed list."""
    u = _browse_subquery(tab, kind)
    base = select(u)
    if q:
        term = f"%{q.lower()}%"
        base = base.where(or_(
            func.lower(u.c.title).like(term),
            func.lower(u.c.author).like(term),
            func.lower(u.c.series).like(term),
            func.lower(u.c.alt_title).like(term),
            func.lower(u.c.alt_author).like(term),
            func.lower(u.c.alt_series).like(term),
        ))
    if author:
        base = base.where(u.c.author == author)
    if series:
        base = base.where(u.c.series == series)

    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await db.execute(
        base.order_by(*_browse_order(u, sort, direction)).offset((page - 1) * limit).limit(limit)
    )).all()
    items = await _hydrate_items(db, rows)
    return Page(items=items, total=total, page=page, limit=limit)


async def _count(db, stmt) -> int:
    return (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()


async def facets_impl(
    db: AsyncSession, tab: LibraryTab, kind: Optional[LibraryItemKind],
) -> LibraryFacets:
    """Body of `GET /facets`: the pills for one tab plus the library-wide counts."""
    u = _browse_subquery(tab, kind)

    async def facet(col):
        rows = (await db.execute(
            select(col, func.count()).where(col.isnot(None)).group_by(col).order_by(col)
        )).all()
        return [FacetCount(name=name, count=count) for name, count in rows]

    authors = await facet(u.c.author)
    series = await facet(u.c.series)

    unpaired_ebooks = await _count(db, select(EBook.id).where(~_paired_ebook()))
    unpaired_audiobooks = await _count(db, select(AudioBook.id).where(~_paired_audiobook()))
    counts = LibraryCounts(
        ebooks=await _count(db, select(EBook.id)),
        audiobooks=await _count(db, select(AudioBook.id)),
        pairs=await _count(db, select(BookPair.id)),
        unpaired=unpaired_ebooks + unpaired_audiobooks,
        new_ebooks=await _count(db, select(EBook.id).where(EBook.acknowledged == False)),  # noqa: E712
        new_audiobooks=await _count(db, select(AudioBook.id).where(AudioBook.acknowledged == False)),  # noqa: E712
        new_pairs=await _count(db, select(BookPair.id).where(BookPair.acknowledged == False)),  # noqa: E712
    )
    return LibraryFacets(authors=authors, series=series, counts=counts)
