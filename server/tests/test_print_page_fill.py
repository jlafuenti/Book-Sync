"""Issue #739: fill ebooks' print page counts from Google Books in a background job.

The job itself (`services/print_pages.run`) is awaited directly with a fake
Google Books search, so no test touches the network; the router tests start it
through the endpoint and wait for it the same way.
"""
import asyncio

import pytest
from sqlalchemy import select, update

from config import settings as app_settings
from database import async_session
from models.book import EBook
from routers import library
from services import print_pages
from tests.factories import make_ebook


def vol(title, authors=("Robin Hobb",), pages=500):
    return {"title": title, "authors": list(authors), "pageCount": pages}


class FakeGoogle:
    """Answers by query; records every query it was asked."""

    def __init__(self, answers=None, default=()):
        self.answers = answers or {}
        self.default = list(default)
        self.queries = []

    async def __call__(self, q):
        self.queries.append(q)
        answer = self.answers.get(q, self.default)
        if isinstance(answer, Exception):
            raise answer
        if callable(answer):
            return await answer()
        return list(answer)


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(print_pages, "REQUEST_SPACING_S", 0)
    print_pages.reset()
    yield
    print_pages.reset()


async def _book(db, book_id):
    async with async_session() as s:
        return (await s.execute(select(EBook).where(EBook.id == book_id))).scalar_one()


# ---------- matching rules ----------

@pytest.mark.parametrize("raw,want", [
    ("978-0-553-57565-4", "9780553575654"),
    ("0553575651", "0553575651"),
    ("055357565x", "055357565X"),
    ("ISBN 9780553575654", "9780553575654"),
    ("12345", None),
    ("", None),
    (None, None),
])
def test_isbns_are_normalised(raw, want):
    assert print_pages.normalize_isbn(raw) == want


def test_an_isbn_answer_takes_the_first_real_page_count():
    volumes = [vol("Anything", pages=0), vol("Anything", pages=12), vol("Anything", pages=789)]
    assert print_pages.pick_page_count(volumes, title=None, author=None, by_isbn=True) == 789


@pytest.mark.parametrize("ebook_title,ebook_author", [
    ("Ship of Destiny", "Robin Hobb"),
    ("Ship of Destiny (Liveship Traders 3)", "Robin Hobb"),
    ("Ship of Destiny: The Liveship Traders", "Hobb, Robin"),
    ("ship of destiny", "Robin Hobb & Someone Else"),
])
def test_a_title_answer_needs_the_same_title_and_author(ebook_title, ebook_author):
    volumes = [vol("Ship of Magic", pages=100), vol("Ship of Destiny", pages=789)]
    got = print_pages.pick_page_count(volumes, title=ebook_title, author=ebook_author, by_isbn=False)
    assert got == 789


@pytest.mark.parametrize("ebook_title,ebook_author", [
    ("Ship of Destiny", "Someone Else"),
    ("Ship of Destinies", "Robin Hobb"),
    ("Ship of Destiny", None),
])
def test_a_title_answer_without_an_exact_match_is_no_answer(ebook_title, ebook_author):
    volumes = [vol("Ship of Destiny", pages=789)]
    got = print_pages.pick_page_count(volumes, title=ebook_title, author=ebook_author, by_isbn=False)
    assert got is None


@pytest.mark.parametrize("author,want", [
    ("Robin Hobb", "intitle:ship of destiny inauthor:robin hobb"),
    ("Robin Hobb & Someone Else", "intitle:ship of destiny inauthor:robin hobb"),
    ("Robin Hobb; Someone Else", "intitle:ship of destiny inauthor:robin hobb"),
    ("Robin Hobb and Someone Else", "intitle:ship of destiny inauthor:robin hobb"),
])
def test_the_title_search_names_the_first_author(author, want):
    assert print_pages.title_query("Ship of Destiny (Liveship Traders 3)", author) == want


async def test_an_isbn_hit_needs_no_title_search():
    google = FakeGoogle({"isbn:9780553575654": [vol("Ship of Destiny")]})
    got = await print_pages.look_up("Ship of Destiny", "Robin Hobb", "978-0-553-57565-4", search=google)
    assert got == 500
    assert google.queries == ["isbn:9780553575654"]


async def test_an_isbn_miss_falls_back_to_the_title():
    google = FakeGoogle({print_pages.title_query("Ship of Destiny", "Robin Hobb"): [vol("Ship of Destiny", pages=640)]})
    got = await print_pages.look_up("Ship of Destiny", "Robin Hobb", "9780553575654", search=google)
    assert got == 640
    assert len(google.queries) == 2


async def test_no_isbn_and_no_author_asks_nothing():
    google = FakeGoogle()
    assert await print_pages.look_up("Ship of Destiny", None, None, search=google) is None
    assert google.queries == []


# ---------- the job ----------

async def test_the_job_fills_counts_and_remembers_what_it_tried(db):
    hit = await make_ebook(db, title="Ship of Destiny", author="Robin Hobb")
    miss = await make_ebook(db, title="Unknown Book", author="Nobody Known")
    by_isbn = await make_ebook(db, title="Anything", author="Anyone", isbn="9780553575654")
    already = await make_ebook(db, title="Ship of Destiny", author="Robin Hobb", print_page_count=321)
    google = FakeGoogle({
        "isbn:9780553575654": [vol("Anything", pages=410)],
        print_pages.title_query("Ship of Destiny", "Robin Hobb"): [vol("Ship of Destiny", pages=789)],
    })

    await print_pages.run(search=google)

    assert (await _book(db, hit.id)).print_page_count == 789
    assert (await _book(db, by_isbn.id)).print_page_count == 410
    assert (await _book(db, miss.id)).print_page_count is None
    assert (await _book(db, already.id)).print_page_count == 321
    for b in (hit, miss, by_isbn):
        assert (await _book(db, b.id)).print_pages_looked_up_at is not None
    assert (await _book(db, already.id)).print_pages_looked_up_at is None
    # ISBN books go first: they are the reliable ones if the quota runs out.
    assert google.queries[0] == "isbn:9780553575654"
    progress = print_pages.get_progress()
    assert (progress["found"], progress["no_match"], progress["errors"]) == (2, 1, 0)
    assert (progress["current"], progress["total"]) == (3, 3)
    assert progress["running"] is False
    assert progress["stopped_reason"] is None
    assert await print_pages.remaining() == 0

    # A second run asks nothing: everything left was already tried.
    again = FakeGoogle()
    await print_pages.run(search=again)
    assert again.queries == []


async def test_a_count_set_by_hand_during_the_run_wins(db):
    book = await make_ebook(db, title="Ship of Destiny", author="Robin Hobb")

    async def edited_meanwhile():
        async with async_session() as s:
            await s.execute(update(EBook).where(EBook.id == book.id).values(print_page_count=123))
            await s.commit()
        return [vol("Ship of Destiny", pages=789)]

    google = FakeGoogle({print_pages.title_query("Ship of Destiny", "Robin Hobb"): edited_meanwhile})
    await print_pages.run(search=google)

    assert (await _book(db, book.id)).print_page_count == 123


async def test_the_daily_limit_stops_the_run_and_leaves_the_book_to_retry(db, monkeypatch):
    monkeypatch.setattr(app_settings, "google_books_api_key", "k")
    first = await make_ebook(db, title="First", author="Author One")
    second = await make_ebook(db, title="Second", author="Author Two")
    google = FakeGoogle({print_pages.title_query("First", "Author One"): print_pages.QuotaExceeded("429")})

    await print_pages.run(search=google)

    progress = print_pages.get_progress()
    assert progress["stopped_reason"] == "quota"
    assert "limit" in progress["message"]
    assert (await _book(db, first.id)).print_pages_looked_up_at is None
    assert (await _book(db, second.id)).print_pages_looked_up_at is None
    assert len(google.queries) == 1


async def test_without_a_key_the_limit_message_points_at_the_key(db, monkeypatch):
    monkeypatch.setattr(app_settings, "google_books_api_key", None)
    await make_ebook(db, title="First", author="Author One")
    google = FakeGoogle({print_pages.title_query("First", "Author One"): print_pages.QuotaExceeded("429")})

    await print_pages.run(search=google)

    assert "System page" in print_pages.get_progress()["message"]


async def test_errors_leave_books_to_retry_and_five_in_a_row_stop_the_run(db):
    for i in range(7):
        await make_ebook(db, title=f"Book {i}", author="Some Author")
    queries = []

    async def failing(q):
        queries.append(q)
        raise print_pages.LookupFailed("502")

    await print_pages.run(search=failing)

    progress = print_pages.get_progress()
    assert progress["stopped_reason"] == "errors"
    assert progress["errors"] == 5
    assert len(queries) == 5
    assert await print_pages.remaining() == 7


async def test_one_error_is_counted_and_the_run_goes_on(db):
    bad = await make_ebook(db, title="Bad", author="Some Author")
    good = await make_ebook(db, title="Good", author="Some Author")
    google = FakeGoogle({
        print_pages.title_query("Bad", "Some Author"): print_pages.LookupFailed("502"),
        print_pages.title_query("Good", "Some Author"): [vol("Good", authors=("Some Author",), pages=222)],
    })

    await print_pages.run(search=google)

    assert (await _book(db, bad.id)).print_pages_looked_up_at is None
    assert (await _book(db, good.id)).print_page_count == 222
    assert print_pages.get_progress()["errors"] == 1
    assert print_pages.get_progress()["stopped_reason"] is None


async def test_cancel_stops_after_the_current_book(db):
    await make_ebook(db, title="First", author="Author One")
    second = await make_ebook(db, title="Second", author="Author Two")

    async def cancel_then_answer():
        print_pages.request_cancel()
        return []

    google = FakeGoogle({print_pages.title_query("First", "Author One"): cancel_then_answer})
    await print_pages.run(search=google)

    assert print_pages.get_progress()["stopped_reason"] == "cancelled"
    assert (await _book(db, second.id)).print_pages_looked_up_at is None


# ---------- the endpoints ----------

@pytest.fixture
async def clients(make_client, make_user, auth_header):
    editor = await make_user(username="ed", role="editor")
    viewer = await make_user(username="vi", role="viewer")
    async with make_client(library.router) as c:
        yield c, auth_header(editor), auth_header(viewer)


async def _wait_until_idle():
    for _ in range(200):
        if not print_pages.get_progress()["running"]:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("print page job did not finish")


async def test_an_editor_starts_the_job_and_reads_its_progress(db, clients, monkeypatch):
    c, editor, _ = clients
    book = await make_ebook(db, title="Ship of Destiny", author="Robin Hobb")
    gate = asyncio.Event()

    async def slow(q):
        await gate.wait()
        return [vol("Ship of Destiny", pages=789)]

    monkeypatch.setattr(print_pages, "google_volumes", slow)

    before = await c.get("/api/library/print-pages/status", headers=editor)
    assert before.status_code == 200
    assert before.json()["remaining"] == 1

    started = await c.post("/api/library/print-pages/start", headers=editor)
    assert started.status_code == 202, started.text
    again = await c.post("/api/library/print-pages/start", headers=editor)
    assert again.status_code == 409

    gate.set()
    await _wait_until_idle()
    after = (await c.get("/api/library/print-pages/status", headers=editor)).json()
    assert after["found"] == 1
    assert after["remaining"] == 0
    assert (await _book(db, book.id)).print_page_count == 789


async def test_the_status_says_whether_a_google_books_key_is_set(clients, monkeypatch):
    c, editor, _ = clients
    monkeypatch.setattr(app_settings, "google_books_api_key", None)
    assert (await c.get("/api/library/print-pages/status", headers=editor)).json()["api_key_configured"] is False
    monkeypatch.setattr(app_settings, "google_books_api_key", "k")
    assert (await c.get("/api/library/print-pages/status", headers=editor)).json()["api_key_configured"] is True


async def test_a_viewer_cannot_start_or_cancel_it(clients):
    c, _, viewer = clients
    assert (await c.post("/api/library/print-pages/start", headers=viewer)).status_code == 403
    assert (await c.post("/api/library/print-pages/cancel", headers=viewer)).status_code == 403


async def test_cancel_through_the_endpoint(db, clients, monkeypatch):
    c, editor, _ = clients
    await make_ebook(db, title="First", author="Author One")
    await make_ebook(db, title="Second", author="Author Two")
    gate = asyncio.Event()

    async def slow(q):
        await gate.wait()
        return []

    monkeypatch.setattr(print_pages, "google_volumes", slow)
    await c.post("/api/library/print-pages/start", headers=editor)
    cancelled = await c.post("/api/library/print-pages/cancel", headers=editor)
    assert cancelled.status_code == 200
    gate.set()
    await _wait_until_idle()
    assert print_pages.get_progress()["stopped_reason"] == "cancelled"
