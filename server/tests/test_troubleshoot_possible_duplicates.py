"""
`possible_duplicate` — content-similarity duplicate candidates (issue #692).

The exact-hash `duplicate` category only catches byte-identical files, so a
remux, a re-tag, or Tandem's own EPUB metadata write-back (#533) makes a real
duplicate invisible to it. This category adds two weaker signals — audiobook
duration, ebook file size — but neither is trusted alone: checked against a
real library, duration or size matches turn up between clearly unrelated
books purely by chance on a large enough collection. So every candidate here
also needs a second, independent signal already on the row (title, author,
narrator, ASIN/ISBN) before it is reported — see the module comment above
`_build_possible_duplicate_category` in `routers/troubleshoot.py` for the
exact tolerance and the reasoning behind it.

Report-only: no delete/bulk-delete action exists for this category (see
`web/src/pages/TroubleshootPage.jsx`).
"""

from routers import troubleshoot
from tests.factories import make_audiobook, make_ebook


async def _issues(make_client, editor, auth_header):
    async with make_client(troubleshoot.router) as c:
        resp = await c.get("/api/troubleshoot/issues", headers=auth_header(editor))
    assert resp.status_code == 200, resp.text
    return resp.json()["categories"]


def _ids(rows):
    return {r["item_id"] for r in rows}


# ---------------------------------------------------------------------------
# Audiobooks — duration + corroboration
# ---------------------------------------------------------------------------

async def test_remuxed_audiobook_same_duration_grouped_not_in_duplicate(
    db, make_client, make_user, auth_header
):
    """Same duration to the second, different hash (a remux), matching title
    — the exact case issue #692 was filed over."""
    editor = await make_user(role="editor")
    a = await make_audiobook(db, title="Axis Test",
                              author="An Author", duration_seconds=7200,
                              file_hash="hash-mp3")
    b = await make_audiobook(db, title="Axis Test",
                              author="An Author", duration_seconds=7200,
                              file_hash="hash-m4b")

    cats = await _issues(make_client, editor, auth_header)

    assert _ids(cats["possible_duplicate"]) == {a.id, b.id}
    assert _ids(cats["duplicate"]) == set()
    for row in cats["possible_duplicate"]:
        assert "duration" in row["detail"].lower()
        assert "matching title" in row["detail"]
        assert "Candidate only" in row["detail"]


async def test_near_duration_audiobooks_within_tolerance_grouped_with_narrator_match(
    db, make_client, make_user, auth_header
):
    """The originating case: two containers of one recording, 9 s apart over
    ~33.8 h (121680 s). Same narrator corroborates."""
    editor = await make_user(role="editor")
    a = await make_audiobook(db, title="Some Very Long Book", duration_seconds=121680,
                              narrators="Jane Narrator", file_hash="hash-1")
    b = await make_audiobook(db, title="Some Very Long Book, Retagged", duration_seconds=121671,
                              narrators="Jane Narrator", file_hash="hash-2")

    cats = await _issues(make_client, editor, auth_header)

    assert _ids(cats["possible_duplicate"]) == {a.id, b.id}
    for row in cats["possible_duplicate"]:
        assert "same narrator" in row["detail"]


async def test_dramatization_and_unabridged_not_grouped_despite_matching_metadata(
    db, make_client, make_user, auth_header
):
    """A BBC-style dramatization and an unabridged reading of the same book
    share a title and author but differ hugely in running time — the
    tolerance alone must keep them apart, with no separate rule needed."""
    editor = await make_user(role="editor")
    dramatized = await make_audiobook(
        db, title="Some Novel (Dramatized)", author="An Author", duration_seconds=7200)
    unabridged = await make_audiobook(
        db, title="Some Novel (Unabridged)", author="An Author", duration_seconds=36000)

    cats = await _issues(make_client, editor, auth_header)

    assert dramatized.id not in _ids(cats["possible_duplicate"])
    assert unabridged.id not in _ids(cats["possible_duplicate"])


async def test_close_duration_unrelated_audiobooks_not_grouped_without_corroboration(
    db, make_client, make_user, auth_header
):
    """Two different books whose lengths merely happen to be a few seconds
    apart (the false-positive pattern found on a real library) must not be
    grouped just because the duration gap is inside tolerance."""
    editor = await make_user(role="editor")
    a = await make_audiobook(db, title="A Completely Different Book",
                              author="Author One", narrators="Narrator One",
                              duration_seconds=32_408)
    b = await make_audiobook(db, title="Another Unrelated Title",
                              author="Author Two", narrators="Narrator Two",
                              duration_seconds=32_405)  # 3 s apart, well within tolerance

    cats = await _issues(make_client, editor, auth_header)

    assert a.id not in _ids(cats["possible_duplicate"])
    assert b.id not in _ids(cats["possible_duplicate"])


async def test_short_audiobooks_below_minimum_duration_not_grouped(
    db, make_client, make_user, auth_header
):
    """Below the minimum-duration floor, matching title AND author is still
    not enough — duration is not trusted as a signal at all down there
    (pinned design decision, issue #692)."""
    editor = await make_user(role="editor")
    a = await make_audiobook(db, title="Short Piece", author="Same Author", duration_seconds=600)
    b = await make_audiobook(db, title="Short Piece", author="Same Author", duration_seconds=600)

    cats = await _issues(make_client, editor, auth_header)

    assert a.id not in _ids(cats["possible_duplicate"])
    assert b.id not in _ids(cats["possible_duplicate"])


async def test_audiobooks_sharing_a_hash_stay_out_of_possible_duplicate(
    db, make_client, make_user, auth_header
):
    """An exact hash match is already the certain `duplicate` category —
    reporting it again here would be a double report of a certainty as a
    mere candidate."""
    editor = await make_user(role="editor")
    a = await make_audiobook(db, title="Exact Copy", author="An Author",
                              duration_seconds=7200, file_hash="same-hash")
    b = await make_audiobook(db, title="Exact Copy", author="An Author",
                              duration_seconds=7200, file_hash="same-hash")

    cats = await _issues(make_client, editor, auth_header)

    assert _ids(cats["duplicate"]) == {a.id, b.id}
    assert a.id not in _ids(cats["possible_duplicate"])
    assert b.id not in _ids(cats["possible_duplicate"])


# ---------------------------------------------------------------------------
# Ebooks — file size + corroboration
# ---------------------------------------------------------------------------

async def test_ebooks_same_size_with_series_suffix_and_initials_spacing_grouped(
    db, make_client, make_user, auth_header
):
    """Same size, different hash (Tandem's own metadata write-back signature),
    titles that differ only by a series suffix and authors that differ only
    in initials spacing — both normalisation rules doing their job at once."""
    editor = await make_user(role="editor")
    a = await make_ebook(db, title="The Bartleby Chronicles: 2", author="J. Q. Sampleton",
                          file_size=555_555, file_hash="hash-a")
    b = await make_ebook(db, title="Bartleby Chronicles", author="J.Q. Sampleton",
                          file_size=555_555, file_hash="hash-b")

    cats = await _issues(make_client, editor, auth_header)

    assert _ids(cats["possible_duplicate"]) == {a.id, b.id}
    for row in cats["possible_duplicate"]:
        assert "same file size" in row["detail"]
        assert "different content hash" in row["detail"]


async def test_ebooks_same_size_different_books_not_grouped_without_corroboration(
    db, make_client, make_user, auth_header
):
    """Two unrelated books that merely happen to share a byte-identical file
    size (found on a real library) must not be reported without a second
    signal agreeing."""
    editor = await make_user(role="editor")
    a = await make_ebook(db, title="A Book About Gardening", author="Pat Gardener",
                          file_size=444_444, file_hash="hash-a")
    b = await make_ebook(db, title="A History of Steel", author="Chris Smith",
                          file_size=444_444, file_hash="hash-b")

    cats = await _issues(make_client, editor, auth_header)

    assert a.id not in _ids(cats["possible_duplicate"])
    assert b.id not in _ids(cats["possible_duplicate"])


async def test_ebooks_sharing_a_hash_stay_out_of_possible_duplicate(
    db, make_client, make_user, auth_header
):
    editor = await make_user(role="editor")
    a = await make_ebook(db, title="Exact Copy", author="An Author",
                          file_size=333_333, file_hash="same-hash")
    b = await make_ebook(db, title="Exact Copy", author="An Author",
                          file_size=333_333, file_hash="same-hash")

    cats = await _issues(make_client, editor, auth_header)

    assert _ids(cats["duplicate"]) == {a.id, b.id}
    assert a.id not in _ids(cats["possible_duplicate"])
    assert b.id not in _ids(cats["possible_duplicate"])


# ---------------------------------------------------------------------------
# Pair-aware ordering
# ---------------------------------------------------------------------------

async def test_unpaired_copy_marked_likely_redundant_and_paired_copy_ordered_first(
    db, make_client, make_user, auth_header
):
    from models.book import BookPair, PairStatus

    editor = await make_user(role="editor")
    paired_ab = await make_audiobook(db, title="A Paired Book", author="Author",
                                      duration_seconds=7200, file_hash="hash-1")
    unpaired_ab = await make_audiobook(db, title="A Paired Book", author="Author",
                                        duration_seconds=7200, file_hash="hash-2")
    some_ebook = await make_ebook(db, title="A Paired Book")
    db.add(BookPair(ebook_id=some_ebook.id, audiobook_id=paired_ab.id,
                     status=PairStatus.SYNCED))
    await db.commit()

    cats = await _issues(make_client, editor, auth_header)

    rows = [r for r in cats["possible_duplicate"] if r["item_id"] in (paired_ab.id, unpaired_ab.id)]
    assert len(rows) == 2
    by_id = {r["item_id"]: r for r in rows}
    assert by_id[paired_ab.id]["likely_redundant"] is False
    assert by_id[unpaired_ab.id]["likely_redundant"] is True
    assert "likely" in by_id[unpaired_ab.id]["detail"].lower()
    # Paired copy ordered before the unpaired one within the group.
    assert [r["item_id"] for r in rows] == [paired_ab.id, unpaired_ab.id]


async def test_all_unpaired_group_has_no_likely_redundant_marker(
    db, make_client, make_user, auth_header
):
    """Nothing to prefer when neither copy is paired — no marker either way."""
    editor = await make_user(role="editor")
    a = await make_audiobook(db, title="Neither Paired", author="Author",
                              duration_seconds=7200, file_hash="hash-1")
    b = await make_audiobook(db, title="Neither Paired", author="Author",
                              duration_seconds=7200, file_hash="hash-2")

    cats = await _issues(make_client, editor, auth_header)

    rows = [r for r in cats["possible_duplicate"] if r["item_id"] in (a.id, b.id)]
    assert len(rows) == 2
    assert all(r["likely_redundant"] is False for r in rows)
