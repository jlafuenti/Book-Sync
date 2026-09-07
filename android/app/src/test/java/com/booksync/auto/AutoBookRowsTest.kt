package com.booksync.auto

import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.BookPairEntity
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Room row → browse row, and browse row → playable Media3 item (issue #225).
 *
 * These two mappings used to be private members of `AudioPlayerService`, which
 * is excluded from Kover, so the media id, the seconds→milliseconds duration
 * conversion and which columns feed the title/artist/series were decided where
 * no test could see them. `resolveMediaItem` — the path that decides what
 * Android Auto actually plays — built its items through a *second* copy of the
 * item builder that the browse tree used; both now go through [autoBookItem].
 */
class AutoBookRowsTest {

    private fun pairRow(
        id: Int = 7,
        audiobookId: Int = 70,
        durationSeconds: Int? = 3600,
        series: String? = "Discworld",
    ) = BookPairEntity(
        id = id,
        ebookId = 700,
        ebookTitle = "Ebook title",
        ebookAuthor = "Ebook author",
        ebookFilename = "book.epub",
        ebookFormat = "epub",
        audiobookId = audiobookId,
        audiobookTitle = "Audiobook title",
        audiobookAuthor = "Narrator",
        audiobookFilename = "book.m4b",
        audiobookFormat = "m4b",
        audiobookDurationSeconds = durationSeconds,
        status = "ready",
        audiobookCoverPath = "covers/70.jpg",
        ebookSeries = series,
    )

    private fun audioRow(
        id: Int = 9,
        durationSeconds: Int? = 120,
        series: String? = null,
    ) = AudioBookEntity(
        id = id,
        title = "Standalone title",
        author = "Standalone author",
        filename = "solo.mp3",
        durationSeconds = durationSeconds,
        format = "mp3",
        series = series,
        seriesIndex = null,
        uploadedAt = "2026-01-01",
        coverFilename = "covers/9.jpg",
    )

    // --- Pair rows ---

    @Test
    fun `a pair row keeps the pair id in its media id and carries the audiobook's columns`() {
        val book = pairRow().toAutoBook(resumePositionMs = 4_500L)
        assertEquals("pair_7", book.mediaId)
        assertEquals(7, book.pairId)
        assertEquals(70, book.audiobookId)
        // The audiobook's title and author, not the ebook's: this row is what
        // the car reads aloud and what the notification shows.
        assertEquals("Audiobook title", book.title)
        assertEquals("Narrator", book.author)
        assertEquals("Discworld", book.series)
        assertEquals(4_500L, book.resumePositionMs)
        assertEquals("book.m4b", book.audioFilename)
        assertEquals("covers/70.jpg", book.serverCoverPath)
    }

    @Test
    fun `duration is converted from seconds to milliseconds, and null means zero`() {
        assertEquals(3_600_000L, pairRow(durationSeconds = 3600).toAutoBook(0L).durationMs)
        assertEquals(0L, pairRow(durationSeconds = null).toAutoBook(0L).durationMs)
        assertEquals(120_000L, audioRow(durationSeconds = 120).toAutoBook(0L).durationMs)
        assertEquals(0L, audioRow(durationSeconds = null).toAutoBook(0L).durationMs)
    }

    // --- Standalone rows ---

    @Test
    fun `a standalone row has no pair id and an audiobook media id`() {
        val book = audioRow().toAutoBook(resumePositionMs = 99L)
        assertEquals("audiobook_9", book.mediaId)
        assertNull(book.pairId)
        assertEquals(9, book.audiobookId)
        assertEquals("Standalone title", book.title)
        assertEquals("Standalone author", book.author)
        assertNull(book.series)
        assertEquals(99L, book.resumePositionMs)
        assertEquals("solo.mp3", book.audioFilename)
        assertEquals("covers/9.jpg", book.serverCoverPath)
    }

    // --- The playable item (resolve path) ---

    @Test
    fun `a resolved pair item is playable, not browsable, and titled from the row`() {
        val item = autoBookItem(pairRow().toAutoBook(0L), "https://example.test/70", artwork = null)
        assertEquals("pair_7", item.mediaId)
        assertEquals("Audiobook title", item.mediaMetadata.title.toString())
        assertEquals("Narrator", item.mediaMetadata.artist.toString())
        assertTrue(item.mediaMetadata.isPlayable == true)
        assertFalse(item.mediaMetadata.isBrowsable == true)
    }

    @Test
    fun `a resolved standalone item is playable, not browsable, and titled from the row`() {
        val item = autoBookItem(audioRow().toAutoBook(0L), "https://example.test/9", artwork = null)
        assertEquals("audiobook_9", item.mediaId)
        assertEquals("Standalone title", item.mediaMetadata.title.toString())
        assertEquals("Standalone author", item.mediaMetadata.artist.toString())
        assertTrue(item.mediaMetadata.isPlayable == true)
        assertFalse(item.mediaMetadata.isBrowsable == true)
    }

    @Test
    fun `the resolve path and the browse tree build the same item for the same book`() {
        // One builder, so a book cannot resume at one position from the
        // browse list and another when Auto asks for it by id.
        val book = pairRow().toAutoBook(resumePositionMs = 1_234L)
        val fromBrowse = autoBrowseItems(listOf(book), null, { "https://example.test/70" }).single()
        val fromResolve = autoBookItem(book, "https://example.test/70", artwork = null)
        assertEquals(fromBrowse.mediaId, fromResolve.mediaId)
        assertEquals(fromBrowse.mediaMetadata.title, fromResolve.mediaMetadata.title)
        assertEquals(fromBrowse.mediaMetadata.artist, fromResolve.mediaMetadata.artist)
        assertEquals(fromBrowse.mediaMetadata.mediaType, fromResolve.mediaMetadata.mediaType)
    }
}
