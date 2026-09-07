package com.booksync.auto

import androidx.media3.common.C
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * The small decisions on Android Auto's play path (issue #225): how the voice
 * search index is composed, how a search result set is paged back to the
 * browser, and how a play request's start index and position are normalised.
 *
 * All three were inline in `AudioPlayerService` — the `C.INDEX_UNSET` rule in
 * particular was a real bug (Assistant passes -1, `getOrNull(-1)` is null, the
 * bookmarked position was lost and playback started at 0) that nothing pinned.
 */
class AutoPlayRequestTest {

    private fun book(id: Int, title: String = "Book $id") = AutoBook(
        mediaId = "audiobook_$id",
        title = title,
        audiobookId = id,
    )

    // --- Search index ---

    @Test
    fun `the search index is Continue Listening first, then the rest of the library`() {
        val recent = listOf(book(3), book(1))
        val all = listOf(book(1), book(2), book(3), book(4))
        assertEquals(
            listOf("audiobook_3", "audiobook_1", "audiobook_2", "audiobook_4"),
            autoSearchIndex(recent, all).map { it.mediaId },
        )
    }

    @Test
    fun `a book in Continue Listening appears once, at its recent position`() {
        // The matcher breaks ties in caller order, so a duplicate further down
        // would be harmless for ranking but would make "two Bartlebys" of one.
        val index = autoSearchIndex(recent = listOf(book(2)), all = listOf(book(2), book(2)))
        assertEquals(listOf("audiobook_2"), index.map { it.mediaId })
    }

    @Test
    fun `an empty Continue Listening leaves the library order untouched`() {
        val all = listOf(book(2), book(1))
        assertEquals(all, autoSearchIndex(emptyList(), all))
    }

    // --- Search result paging ---

    @Test
    fun `pages are cut by page size in order`() {
        val all = (1..5).map { book(it) }
        assertEquals(listOf(1, 2), autoSearchPage(all, page = 0, pageSize = 2).map { it.audiobookId })
        assertEquals(listOf(3, 4), autoSearchPage(all, page = 1, pageSize = 2).map { it.audiobookId })
        assertEquals(listOf(5), autoSearchPage(all, page = 2, pageSize = 2).map { it.audiobookId })
    }

    @Test
    fun `a page past the end is empty rather than an exception`() {
        val all = (1..3).map { book(it) }
        assertEquals(emptyList<AutoBook>(), autoSearchPage(all, page = 5, pageSize = 2))
        assertEquals(emptyList<AutoBook>(), autoSearchPage(emptyList<AutoBook>(), page = 0, pageSize = 10))
    }

    // --- Start index ---

    @Test
    fun `INDEX_UNSET and out-of-range start indices fall back to the first item`() {
        assertEquals(0, autoEffectiveStartIndex(C.INDEX_UNSET, itemCount = 3))
        assertEquals(0, autoEffectiveStartIndex(-1, itemCount = 3))
        assertEquals(0, autoEffectiveStartIndex(3, itemCount = 3))
        assertEquals(0, autoEffectiveStartIndex(0, itemCount = 0))
    }

    @Test
    fun `an in-range start index is kept`() {
        assertEquals(2, autoEffectiveStartIndex(2, itemCount = 3))
        assertEquals(0, autoEffectiveStartIndex(0, itemCount = 1))
    }

    // --- Start position ---

    @Test
    fun `an explicit positive start position wins over the bookmark`() {
        assertEquals(5_000L, autoStartPositionMs(requestedMs = 5_000L, resumeMs = 90_000L))
    }

    @Test
    fun `TIME_UNSET and zero both mean resume from the bookmark`() {
        assertEquals(90_000L, autoStartPositionMs(requestedMs = C.TIME_UNSET, resumeMs = 90_000L))
        assertEquals(90_000L, autoStartPositionMs(requestedMs = 0L, resumeMs = 90_000L))
        assertEquals(0L, autoStartPositionMs(requestedMs = C.TIME_UNSET, resumeMs = 0L))
    }
}
