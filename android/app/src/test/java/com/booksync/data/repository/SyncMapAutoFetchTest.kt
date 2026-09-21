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

    // ---- shouldFetchSyncMapFor: what a DownloadWorker run fetches (issue #655) ----

    @Test
    fun `SYNC_MAP always fetches regardless of cache or the ebook setting`() {
        assertEquals(true, SyncMapAutoFetch.shouldFetchSyncMapFor("SYNC_MAP", alreadyCached = true, downloadWithEbookEnabled = false))
        assertEquals(true, SyncMapAutoFetch.shouldFetchSyncMapFor("SYNC_MAP", alreadyCached = false, downloadWithEbookEnabled = false))
    }

    @Test
    fun `AUDIOBOOK always fetches regardless of cache or the ebook setting`() {
        assertEquals(true, SyncMapAutoFetch.shouldFetchSyncMapFor("AUDIOBOOK", alreadyCached = true, downloadWithEbookEnabled = false))
        assertEquals(true, SyncMapAutoFetch.shouldFetchSyncMapFor("AUDIOBOOK", alreadyCached = false, downloadWithEbookEnabled = false))
    }

    @Test
    fun `ALL fetches only when not already cached, regardless of the ebook setting`() {
        assertEquals(true, SyncMapAutoFetch.shouldFetchSyncMapFor("ALL", alreadyCached = false, downloadWithEbookEnabled = false))
        assertEquals(false, SyncMapAutoFetch.shouldFetchSyncMapFor("ALL", alreadyCached = true, downloadWithEbookEnabled = true))
    }

    @Test
    fun `EBOOK fetches only when the download-with-ebook setting is on`() {
        assertEquals(true, SyncMapAutoFetch.shouldFetchSyncMapFor("EBOOK", alreadyCached = false, downloadWithEbookEnabled = true))
        assertEquals(false, SyncMapAutoFetch.shouldFetchSyncMapFor("EBOOK", alreadyCached = false, downloadWithEbookEnabled = false))
    }

    @Test
    fun `EBOOK setting is ignored by every other type`() {
        // The setting is named "with the ebook" on purpose — it must not leak
        // into AUDIOBOOK/ALL/SYNC_MAP decisions.
        assertEquals(true, SyncMapAutoFetch.shouldFetchSyncMapFor("AUDIOBOOK", alreadyCached = false, downloadWithEbookEnabled = false))
        assertEquals(true, SyncMapAutoFetch.shouldFetchSyncMapFor("ALL", alreadyCached = false, downloadWithEbookEnabled = false))
    }

    @Test
    fun `SYNC_MAP_EXPLICIT always fetches regardless of cache or the ebook setting`() {
        // The "Refresh sync data" button (issue #655) — same unconditional
        // shape as the background SYNC_MAP sweep; only the metered gate tells
        // the two apart (see blockedByMeteredConnection below).
        assertEquals(true, SyncMapAutoFetch.shouldFetchSyncMapFor("SYNC_MAP_EXPLICIT", alreadyCached = true, downloadWithEbookEnabled = false))
        assertEquals(true, SyncMapAutoFetch.shouldFetchSyncMapFor("SYNC_MAP_EXPLICIT", alreadyCached = false, downloadWithEbookEnabled = false))
    }

    @Test
    fun `standalone and unknown types never fetch a sync map`() {
        assertEquals(false, SyncMapAutoFetch.shouldFetchSyncMapFor("STANDALONE_EBOOK", alreadyCached = false, downloadWithEbookEnabled = true))
        assertEquals(false, SyncMapAutoFetch.shouldFetchSyncMapFor("STANDALONE_AUDIOBOOK", alreadyCached = false, downloadWithEbookEnabled = true))
        assertEquals(false, SyncMapAutoFetch.shouldFetchSyncMapFor("BOGUS", alreadyCached = false, downloadWithEbookEnabled = true))
    }

    // ---- blockedByMeteredConnection: the Wi-Fi-only setting (issue #655) ----

    @Test
    fun `wifi-only setting blocks a background fetch on a metered connection`() {
        assertEquals(true, SyncMapAutoFetch.blockedByMeteredConnection("SYNC_MAP", wifiOnlyEnabled = true, isMetered = true))
    }

    @Test
    fun `wifi-only setting does not block on an unmetered connection`() {
        assertEquals(false, SyncMapAutoFetch.blockedByMeteredConnection("SYNC_MAP", wifiOnlyEnabled = true, isMetered = false))
    }

    @Test
    fun `disabling wifi-only never blocks, metered or not`() {
        assertEquals(false, SyncMapAutoFetch.blockedByMeteredConnection("SYNC_MAP", wifiOnlyEnabled = false, isMetered = true))
        assertEquals(false, SyncMapAutoFetch.blockedByMeteredConnection("SYNC_MAP", wifiOnlyEnabled = false, isMetered = false))
    }

    @Test
    fun `the streamed-book prefetch sweep is blocked exactly like SYNC_MAP`() {
        // pairsToPrefetchForStreaming's results are enqueued as plain "SYNC_MAP"
        // work (see LibraryViewModel) — same background type, same gate.
        assertEquals(true, SyncMapAutoFetch.blockedByMeteredConnection("SYNC_MAP", wifiOnlyEnabled = true, isMetered = true))
    }

    @Test
    fun `an explicit refresh is never blocked, wifi-only setting or not, metered or not`() {
        // Issue #655 follow-up: a user who tapped "Refresh sync data" asked for
        // it now, on whatever connection is available. Silently doing nothing
        // because "Wi-Fi only" is on looks exactly like a broken button — that
        // setting is a statement about background work, not this.
        assertEquals(false, SyncMapAutoFetch.blockedByMeteredConnection("SYNC_MAP_EXPLICIT", wifiOnlyEnabled = true, isMetered = true))
        assertEquals(false, SyncMapAutoFetch.blockedByMeteredConnection("SYNC_MAP_EXPLICIT", wifiOnlyEnabled = true, isMetered = false))
        assertEquals(false, SyncMapAutoFetch.blockedByMeteredConnection("SYNC_MAP_EXPLICIT", wifiOnlyEnabled = false, isMetered = true))
    }

    // ---- pairsToPrefetchForStreaming: the never-downloaded / streamed case (issue #655) ----

    @Test
    fun `a streamed pair in the candidate set with no cached map is prefetched`() {
        // Neither file is downloaded — this is exactly the gap needsSyncMapFetch
        // leaves, because it requires ebookDownloaded || audiobookDownloaded.
        val streamed = pair(id = 1, status = "synced", ebookDownloaded = false, audiobookDownloaded = false)

        val result = SyncMapAutoFetch.pairsToPrefetchForStreaming(listOf(streamed), candidatePairIds = setOf(1))

        assertEquals(listOf(1), result)
    }

    @Test
    fun `a pair outside the candidate set is never prefetched, even if synced and uncached`() {
        val streamed = pair(id = 1, status = "synced")

        val result = SyncMapAutoFetch.pairsToPrefetchForStreaming(listOf(streamed), candidatePairIds = emptySet())

        assertEquals(emptyList<Int>(), result)
    }

    @Test
    fun `a candidate pair that already has a current cached map is not re-fetched`() {
        val cached = pair(id = 1, status = "synced", syncMapDownloaded = true)

        val result = SyncMapAutoFetch.pairsToPrefetchForStreaming(listOf(cached), candidatePairIds = setOf(1))

        assertEquals(emptyList<Int>(), result)
    }

    @Test
    fun `a candidate pair with a stale (version-bumped) cached map is re-fetched`() {
        // refreshPairs already flips syncMapDownloaded back to false the moment
        // sync_map_version disagrees with the cached one (issue #55) — by the
        // time this function runs, "stale" and "never cached" look identical.
        val staleAfterRetranscription = pair(id = 1, status = "synced", syncMapDownloaded = false)

        val result = SyncMapAutoFetch.pairsToPrefetchForStreaming(listOf(staleAfterRetranscription), candidatePairIds = setOf(1))

        assertEquals(listOf(1), result)
    }

    @Test
    fun `a candidate pair that is not yet synced is not prefetched`() {
        val transcribing = pair(id = 1, status = "transcribing")

        val result = SyncMapAutoFetch.pairsToPrefetchForStreaming(listOf(transcribing), candidatePairIds = setOf(1))

        assertEquals(emptyList<Int>(), result)
    }

    @Test
    fun `pairsToPrefetchForStreaming returns nothing for an empty candidate set`() {
        val pairs = listOf(pair(id = 1, status = "synced"), pair(id = 2, status = "synced"))

        assertEquals(emptyList<Int>(), SyncMapAutoFetch.pairsToPrefetchForStreaming(pairs, candidatePairIds = emptySet()))
    }
}
