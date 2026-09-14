package com.booksync.ui.details

import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.EBookEntity
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * The detail page's hero cover. It read only the audiobook's cover, so a
 * standalone ebook's page showed the placeholder even when the server had a cover.
 */
class BookDetailsCoverTest {

    private val ebook = EBookEntity(
        id = 7, title = "E", author = null, filename = "e.epub", fileSize = null, format = "epub",
        series = null, seriesIndex = null, uploadedAt = "2026-01-01T00:00:00",
        coverFilename = "/api/files/covers/ebook_7.jpg",
    )

    @Test
    fun `a standalone ebook's page shows the ebook cover`() {
        val ui = BookDetailsUi(loading = false, ebook = ebook)

        assertEquals("/api/files/covers/ebook_7.jpg", ui.coverPath)
        // No audiobook, so no cached audiobook cover file to look for.
        assertNull(ui.audiobookIdForCover)
    }

    @Test
    fun `an audiobook's page still shows the audiobook cover`() {
        val audiobook = AudioBookEntity(
            id = 9, title = "A", author = null, filename = "a.m4b", durationSeconds = null, format = "m4b",
            series = null, seriesIndex = null, uploadedAt = "2026-01-01T00:00:00",
            coverFilename = "/api/files/covers/audiobook_9.jpg",
        )

        assertEquals("/api/files/covers/audiobook_9.jpg", BookDetailsUi(loading = false, audiobook = audiobook).coverPath)
    }
}
