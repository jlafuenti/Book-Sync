package com.booksync.data.repository

import com.booksync.data.local.dao.BookPairDao
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.dto.QueueItemResponse
import com.booksync.data.remote.dto.TranscriptionStatus
import com.booksync.data.util.NetworkMonitor
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * What to tell the user before opening/switching a pair (issue #535, feeding
 * the dialog in #536). Null means ready — no dialog needed.
 *
 * [PairReadiness.readinessFor] is the pure decision: a synced pair, or one
 * whose sync map is already cached, is ready regardless of what the queue
 * says; otherwise the matching queue item wins over the bare pair status,
 * since the queue item carries live progress/position the pair status alone
 * doesn't.
 *
 * [TranscriptionRepository.readiness] wires that to Room + the live queue,
 * and must never throw or block a UI action on a queue fetch: offline, it
 * falls back to the pair status alone rather than making a network call.
 */
class PairReadinessTest {

    private fun pair(
        id: Int = 84,
        status: String,
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
        syncMapDownloaded = syncMapDownloaded,
    )

    private fun queueItem(
        bookPairId: Int,
        status: String,
        progress: Float? = null,
        position: Int = 0,
        errorMessage: String? = null,
    ) = QueueItemResponse(
        id = 1,
        book_pair_id = bookPairId,
        status = status,
        priority = 0,
        position = position,
        progress = progress,
        error_message = errorMessage,
        created_at = "2026-01-01T00:00:00Z",
    )

    // ---- readinessFor: pure decision ---------------------------------------

    @Test
    fun `synced pair is ready regardless of queue`() {
        val result = PairReadiness.readinessFor(
            pair = pair(status = "synced"),
            queueItems = listOf(queueItem(bookPairId = 84, status = "pending", position = 5)),
        )
        assertNull(result)
    }

    @Test
    fun `pair with a cached sync map is ready even if not synced yet`() {
        val result = PairReadiness.readinessFor(
            pair = pair(status = "manual_matched", syncMapDownloaded = true),
            queueItems = emptyList(),
        )
        assertNull(result)
    }

    @Test
    fun `manual_matched pair with a pending queue item reports Queued at its position`() {
        val result = PairReadiness.readinessFor(
            pair = pair(id = 84, status = "manual_matched"),
            queueItems = listOf(queueItem(bookPairId = 84, status = "pending", position = 3)),
        )
        assertEquals(TranscriptionStatus.Queued(3), result)
    }

    @Test
    fun `transcribing pair with an in_progress queue item reports live progress`() {
        val result = PairReadiness.readinessFor(
            pair = pair(id = 84, status = "transcribing"),
            queueItems = listOf(queueItem(bookPairId = 84, status = "in_progress", progress = 0.42f)),
        )
        assertEquals(TranscriptionStatus.Transcribing(42), result)
    }

    @Test
    fun `auto_matched pair with no queue item reports NotTranscribed`() {
        val result = PairReadiness.readinessFor(
            pair = pair(id = 84, status = "auto_matched"),
            queueItems = emptyList(),
        )
        assertEquals(TranscriptionStatus.NotTranscribed, result)
    }

    @Test
    fun `error pair with no queue item reports Failed`() {
        val result = PairReadiness.readinessFor(
            pair = pair(id = 84, status = "error"),
            queueItems = emptyList(),
        )
        assertEquals(TranscriptionStatus.Failed(null), result)
    }

    @Test
    fun `queue items for other pairs are ignored`() {
        val result = PairReadiness.readinessFor(
            pair = pair(id = 84, status = "auto_matched"),
            queueItems = listOf(queueItem(bookPairId = 99, status = "pending", position = 1)),
        )
        assertEquals(TranscriptionStatus.NotTranscribed, result)
    }

    // ---- TranscriptionRepository.readiness: wiring -------------------------

    @Test
    fun `readiness offline uses the pair status only and never calls the queue`() = runTest {
        val api = mockk<BookSyncApi>()
        val bookPairDao = mockk<BookPairDao>()
        coEvery { bookPairDao.getPairById(84) } returns pair(id = 84, status = "manual_matched")
        val networkMonitor = mockk<NetworkMonitor>()
        every { networkMonitor.isOnline } returns MutableStateFlow(false)

        val repository = TranscriptionRepository(
            api = api,
            networkMonitor = networkMonitor,
            bookPairDao = bookPairDao,
        )

        val result = repository.readiness(84)

        assertEquals(TranscriptionStatus.NotTranscribed, result)
        coVerify(exactly = 0) { api.getTranscriptionQueue() }
    }

    @Test
    fun `readiness for a synced pair never fetches the queue, even online`() = runTest {
        // Every pair open goes through readiness(); a queue round trip on a pair that is
        // already ready would add network latency to opening any book (issue #536).
        val api = mockk<BookSyncApi>()
        val bookPairDao = mockk<BookPairDao>()
        coEvery { bookPairDao.getPairById(7) } returns pair(id = 7, status = "synced")
        val networkMonitor = mockk<NetworkMonitor>()
        every { networkMonitor.isOnline } returns MutableStateFlow(true)

        val repository = TranscriptionRepository(
            api = api,
            networkMonitor = networkMonitor,
            bookPairDao = bookPairDao,
        )

        assertNull(repository.readiness(7))
        coVerify(exactly = 0) { api.getTranscriptionQueue() }
    }

    @Test
    fun `readiness for a pair with a cached sync map never fetches the queue`() = runTest {
        val api = mockk<BookSyncApi>()
        val bookPairDao = mockk<BookPairDao>()
        coEvery { bookPairDao.getPairById(8) } returns
            pair(id = 8, status = "manual_matched", syncMapDownloaded = true)
        val networkMonitor = mockk<NetworkMonitor>()
        every { networkMonitor.isOnline } returns MutableStateFlow(true)

        val repository = TranscriptionRepository(
            api = api,
            networkMonitor = networkMonitor,
            bookPairDao = bookPairDao,
        )

        assertNull(repository.readiness(8))
        coVerify(exactly = 0) { api.getTranscriptionQueue() }
    }

    @Test
    fun `readiness for an unknown pair id returns null`() = runTest {
        val api = mockk<BookSyncApi>()
        val bookPairDao = mockk<BookPairDao>()
        coEvery { bookPairDao.getPairById(404) } returns null
        val networkMonitor = mockk<NetworkMonitor>()
        every { networkMonitor.isOnline } returns MutableStateFlow(true)

        val repository = TranscriptionRepository(
            api = api,
            networkMonitor = networkMonitor,
            bookPairDao = bookPairDao,
        )

        assertNull(repository.readiness(404))
    }
}
