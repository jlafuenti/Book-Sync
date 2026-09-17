package com.booksync.ui.reader

import com.booksync.data.local.entity.BookPairEntity
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * [selectionSyncAvailable] — issue #597 Track C, "Selection sync while
 * streaming". Before this the reader's "Sync to Audio" selection action
 * required a downloaded audiobook ([ReaderSelectionController.Host.syncToAudioAvailable]
 * read `pair?.audiobookDownloaded == true` directly); now it is available
 * whenever the match can resolve, which `syncSelectionToAudio` (`ReaderSelection.kt`)
 * and [PageAudioHandoff] do through the cached sync map, streaming the
 * audiobook via the player rather than reading a local file.
 */
class ReaderSelectionAvailabilityTest {

    private fun pair(status: String = "synced", audiobookDownloaded: Boolean = false) =
        BookPairEntity(
            id = 1,
            ebookId = 1,
            ebookTitle = "Axis Test",
            ebookAuthor = null,
            ebookFilename = "axis-test.epub",
            ebookFormat = "epub",
            audiobookId = 1,
            audiobookTitle = "Axis Test",
            audiobookAuthor = null,
            audiobookFilename = "axis-test.m4b",
            audiobookFormat = "m4b",
            audiobookDurationSeconds = 3600,
            status = status,
            audiobookDownloaded = audiobookDownloaded,
        )

    @Test
    fun `no pair is never available`() {
        assertFalse(selectionSyncAvailable(pair = null, isOnline = true))
        assertFalse(selectionSyncAvailable(pair = null, isOnline = false))
    }

    @Test
    fun `unsynced pair is never available, online or off, downloaded or not`() {
        assertFalse(selectionSyncAvailable(pair("matched"), isOnline = true))
        assertFalse(selectionSyncAvailable(pair("matched", audiobookDownloaded = true), isOnline = true))
        assertFalse(selectionSyncAvailable(pair("pending"), isOnline = false))
    }

    @Test
    fun `synced pair with a downloaded audiobook is available offline`() {
        assertTrue(selectionSyncAvailable(pair("synced", audiobookDownloaded = true), isOnline = false))
    }

    @Test
    fun `synced pair with a downloaded audiobook is available online too`() {
        assertTrue(selectionSyncAvailable(pair("synced", audiobookDownloaded = true), isOnline = true))
    }

    @Test
    fun `synced pair with nothing downloaded is available online — it streams`() {
        assertTrue(selectionSyncAvailable(pair("synced", audiobookDownloaded = false), isOnline = true))
    }

    @Test
    fun `synced pair with nothing downloaded is unavailable offline`() {
        assertFalse(selectionSyncAvailable(pair("synced", audiobookDownloaded = false), isOnline = false))
    }
}
