package com.booksync.data.repository

import com.booksync.data.local.entity.BookPairEntity
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * The pure decisions behind fetching a sync map on its own (issue #537):
 * which pairs are due for a fetch after a library refresh, and which pair
 * ids just left the active transcription queue. [LibraryViewModel] calls
 * both in one-liners; kept here so they're testable without Hilt/WorkManager.
 */
class SyncMapAutoFetchTest {

    private fun pair(
        id: Int = 84,
        status: String,
        ebookDownloaded: Boolean = false,
        audiobookDownloaded: Boolean = false,
        syncMapDownloaded: Boolean = false,
    ) = BookPairEntity(
        id = id,
        ebookId = 7,
        ebookTitle = "The Mad Ship",
        ebookAuthor = "Robin Hobb",
        ebookFilename = "mad-ship.epub",
        ebookFormat = "epub",
        audiobookId = 9,
        audiobookTitle = "The Mad Ship",
        audiobookAuthor = "Robin Hobb",
        audiobookFilename = "mad-ship.m4b",
        audiobookFormat = "m4b",
        audiobookDurationSeconds = 1_000,
        status = status,
        ebookDownloaded = ebookDownloaded,
        audiobookDownloaded = audiobookDownloaded,
        syncMapDownloaded = syncMapDownloaded,
    )

    // ---- needsSyncMapFetch: status x downloads x cached --------------------

    @Test
    fun `synced pair with ebook downloaded and no cached map needs a fetch`() {
        assertEquals(
            true,
            SyncMapAutoFetch.needsSyncMapFetch(pair(status = "synced", ebookDownloaded = true)),
        )
    }

    @Test
    fun `synced pair with audiobook downloaded and no cached map needs a fetch`() {
        assertEquals(
            true,
            SyncMapAutoFetch.needsSyncMapFetch(pair(status = "synced", audiobookDownloaded = true)),
        )
    }

    @Test
    fun `synced pair with both formats downloaded and no cached map needs a fetch`() {
        assertEquals(
            true,
            SyncMapAutoFetch.needsSyncMapFetch(
                pair(status = "synced", ebookDownloaded = true, audiobookDownloaded = true),
            ),
        )
    }

    @Test
    fun `synced pair with nothing downloaded does not need a fetch`() {
        assertEquals(
            false,
            SyncMapAutoFetch.needsSyncMapFetch(pair(status = "synced")),
        )
    }

    @Test
    fun `synced pair already carrying a cached map does not need a fetch`() {
        assertEquals(
            false,
            SyncMapAutoFetch.needsSyncMapFetch(
                pair(status = "synced", ebookDownloaded = true, syncMapDownloaded = true),
            ),
        )
    }

    @Test
    fun `transcribing pair with ebook downloaded does not need a fetch`() {
        assertEquals(
            false,
            SyncMapAutoFetch.needsSyncMapFetch(pair(status = "transcribing", ebookDownloaded = true)),
        )
    }

    @Test
    fun `manual_matched pair with audiobook downloaded does not need a fetch`() {
        assertEquals(
            false,
            SyncMapAutoFetch.needsSyncMapFetch(pair(status = "manual_matched", audiobookDownloaded = true)),
        )
    }

    @Test
    fun `auto_matched pair with ebook downloaded does not need a fetch`() {
        assertEquals(
            false,
            SyncMapAutoFetch.needsSyncMapFetch(pair(status = "auto_matched", ebookDownloaded = true)),
        )
    }

    @Test
    fun `error pair with ebook downloaded does not need a fetch`() {
        assertEquals(
            false,
            SyncMapAutoFetch.needsSyncMapFetch(pair(status = "error", ebookDownloaded = true)),
        )
    }

    @Test
    fun `unmatched pair with nothing downloaded does not need a fetch`() {
        assertEquals(
            false,
            SyncMapAutoFetch.needsSyncMapFetch(pair(status = "unmatched")),
        )
    }

    // ---- pairsToFetch: the sweep over a whole list -------------------------

    @Test
    fun `pairsToFetch returns only the ids that need a fetch, in list order`() {
        val due = pair(id = 1, status = "synced", ebookDownloaded = true)
        val cached = pair(id = 2, status = "synced", ebookDownloaded = true, syncMapDownloaded = true)
        val notSynced = pair(id = 3, status = "transcribing", ebookDownloaded = true)
        val alsoDue = pair(id = 4, status = "synced", audiobookDownloaded = true)

        val result = SyncMapAutoFetch.pairsToFetch(listOf(due, cached, notSynced, alsoDue))

        assertEquals(listOf(1, 4), result)
    }

    @Test
    fun `pairsToFetch returns nothing for an empty list`() {
        assertEquals(emptyList<Int>(), SyncMapAutoFetch.pairsToFetch(emptyList()))
    }

    // ---- leftActiveSet: queue-exit detection --------------------------------

    @Test
    fun `leftActiveSet is empty on the first emission, previous null`() {
        assertEquals(emptySet<Int>(), SyncMapAutoFetch.leftActiveSet(previous = null, current = setOf(1, 2)))
    }

    @Test
    fun `leftActiveSet is empty when nothing left`() {
        assertEquals(emptySet<Int>(), SyncMapAutoFetch.leftActiveSet(previous = setOf(1, 2), current = setOf(1, 2, 3)))
    }

    @Test
    fun `leftActiveSet reports ids present before and absent now`() {
        assertEquals(setOf(2), SyncMapAutoFetch.leftActiveSet(previous = setOf(1, 2), current = setOf(1)))
    }

    @Test
    fun `leftActiveSet reports every id when the set empties out`() {
        assertEquals(setOf(1, 2), SyncMapAutoFetch.leftActiveSet(previous = setOf(1, 2), current = emptySet()))
    }
}
