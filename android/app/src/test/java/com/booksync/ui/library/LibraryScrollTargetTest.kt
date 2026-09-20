package com.booksync.ui.library

import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.local.entity.EBookEntity
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
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

    // ---- libraryTourTarget: the cell the "open a book" step spotlights ---

    private fun unsynced(id: Int) = pair(id).copy(status = "manual_matched")

    @Test
    fun `the picker's pair wins when it is near the top`() {
        val items = listOf(
            LibraryItem("pair_1", pair = pair(1)),
            LibraryItem("pair_2", pair = pair(2)),
        )
        assertEquals(1, libraryTourTarget(items, preferredPairId = 2))
    }

    @Test
    fun `a picked pair buried in the grid gives way to the first synced pair`() {
        // In practice the picker's choice was the last card of a 300-item grid.
        val items = List(20) { i -> LibraryItem("ebook_$i", ebook = ebook(i)) } +
            LibraryItem("pair_40", pair = unsynced(40)) +
            LibraryItem("pair_41", pair = pair(41)) +
            LibraryItem("pair_99", pair = pair(99))
        assertEquals(21, libraryTourTarget(items, preferredPairId = 99))
    }

    @Test
    fun `with no synced pair in the grid the picked pair is still used`() {
        val items = List(10) { i -> LibraryItem("ebook_$i", ebook = ebook(i)) } +
            LibraryItem("pair_99", pair = pair(99))
        assertEquals(10, libraryTourTarget(items, preferredPairId = 99, nearTop = 3))
    }

    @Test
    fun `nothing to spotlight when the grid has no pairs`() {
        val items = listOf(LibraryItem("ebook_1", ebook = ebook(1)))
        assertNull(libraryTourTarget(items, preferredPairId = null))
    }

    // ---- when the Library may tell the walkthrough it has settled (issue #652) ----

    @Test
    fun `the Library is not settled while a refresh is in flight`() {
        assertFalse(libraryTourSettled(refreshing = true, openPairStep = null, scrolledFor = null))
    }

    @Test
    fun `outside the open-a-book step only the refresh matters`() {
        assertTrue(libraryTourSettled(refreshing = false, openPairStep = null, scrolledFor = null))
    }

    /**
     * The open-a-book step resets the filters, waits for the list and jumps a thousand cells
     * to the tour's pair before the card's control exists. That took 0.9 s on the emulator —
     * past the tour's 600 ms settle window — so the card said the control was missing for
     * 0.2 s. The Library has not settled until that jump has landed.
     */
    @Test
    fun `the open-a-book step is not settled until the jump to that pair has landed`() {
        assertFalse(libraryTourSettled(refreshing = false, openPairStep = 42, scrolledFor = null))
        assertFalse(libraryTourSettled(refreshing = false, openPairStep = 42, scrolledFor = 7))
        assertTrue(libraryTourSettled(refreshing = false, openPairStep = 42, scrolledFor = 42))
    }
}
