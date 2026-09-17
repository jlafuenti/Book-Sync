package com.booksync.ui.library

import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.local.entity.EBookEntity
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Where the tour's picked pair sits in the grid `ItemGrid` lays out (issue #597
 * Track B). `ItemGrid` renders `items` — the flat, ungrouped list — directly,
 * one [LibraryItem] per grid cell with no series headers mixed in, so the index
 * into that same list is exactly what `LazyGridState.animateScrollToItem` needs.
 */
class LibraryScrollTargetTest {

    private fun pair(id: Int) = BookPairEntity(
        id = id, ebookId = id, ebookTitle = "Pair $id", ebookAuthor = null, ebookFilename = "e$id.epub",
        ebookFormat = "epub", audiobookId = id, audiobookTitle = "Pair $id", audiobookAuthor = null,
        audiobookFilename = "a$id.m4b", audiobookFormat = "m4b", audiobookDurationSeconds = null,
        status = "synced",
    )

    private fun ebook(id: Int) = EBookEntity(
        id = id, title = "Ebook $id", author = null, filename = "e$id.epub", fileSize = null,
        format = "epub", series = null, seriesIndex = null, uploadedAt = "2026-01-01T00:00:00",
    )

    private fun audiobook(id: Int) = AudioBookEntity(
        id = id, title = "Audiobook $id", author = null, filename = "a$id.m4b", durationSeconds = null,
        format = "m4b", series = null, seriesIndex = null, uploadedAt = "2026-01-01T00:00:00",
    )

    @Test
    fun `finds the pair's index among standalone items`() {
        val items = listOf(
            LibraryItem("ebook_10", ebook = ebook(10)),
            LibraryItem("audiobook_11", audiobook = audiobook(11)),
            LibraryItem("pair_5", pair = pair(5)),
            LibraryItem("ebook_12", ebook = ebook(12)),
        )
        assertEquals(2, libraryIndexOf(items, pairId = 5))
    }

    @Test
    fun `returns null when no item carries that pair`() {
        val items = listOf(
            LibraryItem("ebook_10", ebook = ebook(10)),
            LibraryItem("pair_5", pair = pair(5)),
        )
        assertNull(libraryIndexOf(items, pairId = 999))
    }

    @Test
    fun `a standalone item never matches even if its own id equals the pair id`() {
        // A LibraryItem's key encodes its entity kind, but ebook/audiobook ids
        // and pair ids are independent sequences server-side — nothing stops a
        // coincidence. Only `pair?.id` may match; an ebook or audiobook whose id
        // happens to equal the pair id must be skipped.
        val items = listOf(
            LibraryItem("ebook_5", ebook = ebook(5)),
            LibraryItem("audiobook_5", audiobook = audiobook(5)),
        )
        assertNull(libraryIndexOf(items, pairId = 5))
    }

    @Test
    fun `an empty list has no index`() {
        assertNull(libraryIndexOf(emptyList(), pairId = 1))
    }
}
