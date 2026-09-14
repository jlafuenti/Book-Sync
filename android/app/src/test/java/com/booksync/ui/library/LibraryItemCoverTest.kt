package com.booksync.ui.library

import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.local.entity.EBookEntity
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Which cover a Library card shows. The grid and the series stacks both read
 * [LibraryItem.coverPath]; before it existed each grid inlined a rule with no
 * ebook branch, so a standalone ebook never had a cover even though the server
 * sends one.
 */
class LibraryItemCoverTest {

    private val ebook = EBookEntity(
        id = 1, title = "E", author = null, filename = "e.epub", fileSize = null, format = "epub",
        series = null, seriesIndex = null, uploadedAt = "2026-01-01T00:00:00",
        coverFilename = "/api/files/covers/ebook_1.jpg",
    )

    private val audiobook = AudioBookEntity(
        id = 2, title = "A", author = null, filename = "a.m4b", durationSeconds = null, format = "m4b",
        series = null, seriesIndex = null, uploadedAt = "2026-01-01T00:00:00",
        coverFilename = "/api/files/covers/audiobook_2.jpg",
    )

    private val pair = BookPairEntity(
        id = 3, ebookId = 1, ebookTitle = "E", ebookAuthor = null, ebookFilename = "e.epub",
        ebookFormat = "epub", audiobookId = 2, audiobookTitle = "A", audiobookAuthor = null,
        audiobookFilename = "a.m4b", audiobookFormat = "m4b", audiobookDurationSeconds = null,
        status = "synced", audiobookCoverPath = "/api/files/covers/audiobook_2.jpg",
    )

    @Test
    fun `a standalone ebook uses its own cover`() {
        assertEquals("/api/files/covers/ebook_1.jpg", LibraryItem("ebook_1", ebook = ebook).coverPath)
    }

    @Test
    fun `pairs and audiobooks keep the audiobook cover`() {
        assertEquals("/api/files/covers/audiobook_2.jpg", LibraryItem("pair_3", pair = pair).coverPath)
        assertEquals("/api/files/covers/audiobook_2.jpg", LibraryItem("audiobook_2", audiobook = audiobook).coverPath)
    }

    @Test
    fun `an ebook without a cover has none`() {
        assertNull(LibraryItem("ebook_1", ebook = ebook.copy(coverFilename = null)).coverPath)
    }
}
