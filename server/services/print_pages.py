"""Fill ebooks' print page counts from Google Books (issue #739).

The reader's print pages (issue #730) come from `ebooks.print_page_count` when
the EPUB embeds no page list, and until now only an editor set it, one book at
a time. This is the library-wide pass: a background, cancelable, single-instance
job, modelled on `library_verify`, that looks up every ebook without a count.

The rules, each pinned by `tests/test_print_page_fill.py`:

* **By ISBN first.** An ISBN names an edition, so Google's `pageCount` for it is
  taken as-is. ISBN books also go first in the run, so the reliable lookups are
  the ones that happen if the daily quota runs out part-way.
* **Otherwise by title and author, exactly.** Google returns many editions and
  near-namesakes; a result counts only when its normalised title equals the
  ebook's and one of its authors' surnames is among the ebook author's names.
  Anything less is no answer, and the book is left for an editor.
* **Never overwrites.** The write is `UPDATE ... WHERE print_page_count IS NULL`,
  so a count an editor sets while the job runs wins.
* **Remembers what it tried.** `print_pages_looked_up_at` is stamped on every
  book that got an answer, found or not; the next run skips those. A book whose
  lookup failed (network, 5xx) or hit the quota is not stamped, so it is retried.
* **Stops on the quota.** Google Books allows about 1,000 requests a day per
  key; a 429 or a quota 403 ends the run with a message saying to run it again
  tomorrow. Five failures in a row end it too.

Not the library job guard (`library_jobs`): that exists to stop two walks of
the *files* racing on the `file_path` unique index, and this job reads no files
and writes one column the scans never touch. Holding it would lock scans out
for the half hour a full run takes, for no protection.
"""

import asyncio
import logging
import re
import unicodedata
from typing import Awaitable, Callable, Optional

import httpx
from sqlalchemy import func, select, update

from config import settings
from database import async_session
from models.book import EBook
from utils import utcnow

logger = logging.getLogger("print-pages")

GOOGLE_BOOKS_URL = "https://www.googleapis.com/books/v1/volumes"
# Google's pageCount is sometimes 0 or a handful for ebook editions: noise.
MIN_PAGES = 20
# Spaced out so a run stays well under Google's per-minute limit.
REQUEST_SPACING_S = 0.5
MAX_CONSECUTIVE_ERRORS = 5
WRITE_BATCH = 25
_QUOTA_REASONS = {"dailyLimitExceeded", "rateLimitExceeded", "quotaExceeded", "userRateLimitExceeded"}


class QuotaExceeded(Exception):
    """Google Books refused for quota: the run stops, the book is retried next run."""


class LookupFailed(Exception):
    """This lookup did not get an answer (network, 5xx): the book is retried next run."""


Search = Callable[[str], Awaitable[list]]


async def google_volumes(q: str) -> list:
    """The `volumeInfo` of each Google Books result for query `q`."""
    params = {"q": q, "maxResults": 20, "printType": "books"}
    if settings.google_books_api_key:
        params["key"] = settings.google_books_api_key
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(GOOGLE_BOOKS_URL, params=params, timeout=15.0)
    except httpx.HTTPError as e:
        raise LookupFailed(f"Google Books unreachable: {e.__class__.__name__}") from e
    if resp.status_code == 429:
        raise QuotaExceeded("Google Books rate limit (429)")
    if resp.status_code == 403:
        try:
            reasons = {err.get("reason") for err in resp.json()["error"]["errors"]}
        except Exception:
            reasons = set()
        if reasons & _QUOTA_REASONS:
            raise QuotaExceeded("Google Books quota (403)")
    if resp.status_code != 200:
        raise LookupFailed(f"Google Books answered {resp.status_code}")
    try:
        items = resp.json().get("items", [])
    except ValueError as e:
        raise LookupFailed("Google Books answered with something that is not JSON") from e
    return [item.get("volumeInfo", {}) for item in items]


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def normalize_isbn(raw: Optional[str]) -> Optional[str]:
    """Digits (and a final X) of a 10- or 13-character ISBN, or None."""
    if not raw:
        return None
    s = re.sub(r"[^0-9Xx]", "", raw).upper()
    if len(s) == 13 and s.isdigit():
        return s
    if len(s) == 10 and s[:9].isdigit() and (s[9].isdigit() or s[9] == "X"):
        return s
    return None


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    text = text.replace("&", " and ")
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


def _main_title(title: str) -> str:
    """The title without a series tag in brackets or a subtitle after `:`."""
    title = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]\s*", " ", title)
    return _fold(title.split(":", 1)[0])


def _author_names(author: str) -> set:
    return set(_fold(author).split())


def pick_page_count(volumes: list, *, title: Optional[str], author: Optional[str], by_isbn: bool) -> Optional[int]:
    """The page count to take from one search's results, or None."""
    for v in volumes:
        pages = v.get("pageCount") or 0
        if not isinstance(pages, int) or pages < MIN_PAGES:
            continue
        if by_isbn:
            return pages
        if not title or not author:
            return None
        if _main_title(v.get("title") or "") != _main_title(title):
            continue
        names = _author_names(author)
        surnames = {(_fold(a).split() or [""])[-1] for a in v.get("authors") or []}
        if surnames & names:
            return pages
    return None


def title_query(title: str, author: str) -> str:
    """The title search: the main title and the first of the ebook's authors."""
    first_author = re.split(r"\s*(?:&|;|\band\b)\s*", author, maxsplit=1)[0]
    return f"intitle:{_main_title(title)} inauthor:{_fold(first_author)}"


async def look_up(title: Optional[str], author: Optional[str], isbn: Optional[str],
                  search: Search = None) -> Optional[int]:
    """One ebook's print page count from Google Books, or None when there is no
    trustworthy answer. Raises `QuotaExceeded` / `LookupFailed`."""
    search = search or google_volumes
    code = normalize_isbn(isbn)
    if code:
        found = pick_page_count(await search(f"isbn:{code}"), title=None, author=None, by_isbn=True)
        if found:
            return found
    if not title or not author:
        return None
    return pick_page_count(await search(title_query(title, author)), title=title, author=author, by_isbn=False)


# ---------------------------------------------------------------------------
# The job (module singleton, like library_verify)
# ---------------------------------------------------------------------------

_MESSAGES = {
    None: "Finished.",
    "quota": "Stopped: Google Books' daily limit is reached. Run it again tomorrow to carry on.",
    "errors": "Stopped: Google Books did not answer several times in a row. Try again later.",
    "cancelled": "Cancelled.",
    "quota-no-key": "Stopped: Google Books refused. Without an API key its limit is very low; set "
                    "GOOGLE_BOOKS_API_KEY on the server and run it again.",
    "error": "Stopped by an unexpected error.",
}


def _idle_state() -> dict:
    return {
        "running": False, "current": 0, "total": 0, "found": 0, "no_match": 0, "errors": 0,
        "started_at": None, "finished_at": None, "cancel_requested": False,
        "stopped_reason": None, "message": None, "last_error": None,
    }


_state = _idle_state()
_task: Optional[asyncio.Task] = None


def get_progress() -> dict:
    return dict(_state)


def api_key_configured() -> bool:
    """Whether a Google Books API key is set; without one Google refuses almost at once."""
    return bool(settings.google_books_api_key)


def reset() -> None:
    """Forget the last run. For tests."""
    _state.clear()
    _state.update(_idle_state())


def request_cancel() -> None:
    if _state["running"]:
        _state["cancel_requested"] = True


def _eligible():
    return (EBook.print_page_count.is_(None), EBook.print_pages_looked_up_at.is_(None))


async def remaining() -> int:
    """Ebooks the next run would look up."""
    async with async_session() as db:
        return (await db.execute(select(func.count()).select_from(EBook).where(*_eligible()))).scalar_one()


async def start() -> bool:
    """Start a run in the background. False when one is already running."""
    global _task
    if _state["running"]:
        return False
    _begin()
    _task = asyncio.create_task(_run())
    return True


def _begin() -> None:
    _state.update(_idle_state(), running=True, started_at=utcnow().isoformat())


async def run(search: Search = None) -> None:
    """One whole run, awaited. `start()` runs this in the background."""
    _begin()
    await _run(search)


async def _write(answers: list) -> None:
    """Record a batch of `(ebook_id, page_count_or_None)` answers."""
    if not answers:
        return
    now = utcnow()
    async with async_session() as db:
        for ebook_id, pages in answers:
            if pages:
                await db.execute(update(EBook)
                                 .where(EBook.id == ebook_id, EBook.print_page_count.is_(None))
                                 .values(print_page_count=pages))
            await db.execute(update(EBook).where(EBook.id == ebook_id).values(print_pages_looked_up_at=now))
        await db.commit()
    answers.clear()


async def _run(search: Search = None) -> None:
    # Looked up at call time, so a test's monkeypatch of google_volumes applies.
    search = search or google_volumes
    pending: list = []
    stopped = None
    try:
        async with async_session() as db:
            has_isbn = func.coalesce(func.length(EBook.isbn), 0) > 0
            rows = (await db.execute(
                select(EBook.id, EBook.title, EBook.author, EBook.isbn)
                .where(*_eligible())
                .order_by(has_isbn.desc(), EBook.id)
            )).all()
        _state["total"] = len(rows)
        consecutive_errors = 0
        for i, (ebook_id, title, author, isbn) in enumerate(rows):
            if _state["cancel_requested"]:
                stopped = "cancelled"
                break
            if i and REQUEST_SPACING_S:
                await asyncio.sleep(REQUEST_SPACING_S)
            try:
                pages = await look_up(title, author, isbn, search=search)
            except QuotaExceeded as e:
                logger.warning("[print-pages] %s; stopping after %d books", e, i)
                stopped = "quota"
                break
            except LookupFailed as e:
                _state["errors"] += 1
                _state["current"] += 1
                _state["last_error"] = str(e)
                consecutive_errors += 1
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    stopped = "errors"
                    break
                continue
            consecutive_errors = 0
            _state["found" if pages else "no_match"] += 1
            _state["current"] += 1
            pending.append((ebook_id, pages))
            if len(pending) >= WRITE_BATCH:
                await _write(pending)
            if _state["cancel_requested"]:
                stopped = "cancelled"
                break
        await _write(pending)
    except Exception as e:
        logger.exception("[print-pages] run failed")
        stopped = "error"
        _state["last_error"] = str(e)
    finally:
        no_key = stopped == "quota" and not settings.google_books_api_key
        _state.update(running=False, finished_at=utcnow().isoformat(),
                      stopped_reason=stopped, message=_MESSAGES["quota-no-key" if no_key else stopped])
        logger.info("[print-pages] %s found=%d no_match=%d errors=%d",
                    stopped or "finished", _state["found"], _state["no_match"], _state["errors"])
