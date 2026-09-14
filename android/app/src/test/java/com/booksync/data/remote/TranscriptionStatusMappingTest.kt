package com.booksync.data.remote

import com.booksync.data.local.dao.BookPairDao
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.dto.QueueItemResponse
import com.booksync.data.remote.dto.TranscriptionStatus
import com.booksync.data.repository.TranscriptionRepository
import com.booksync.data.util.NetworkMonitor
import io.mockk.coEvery
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * The server's real transcription status vocabularies (issue #535):
 *
 *   pair `status`  (`server/models/book.py`, `PairStatus`):
 *     unmatched | auto_matched | manual_matched | transcribing | synced | error
 *   queue item `status` (`server/models/transcription_queue.py`):
 *     pending | in_progress | completed | failed | cancelled
 *
 * `TranscriptionStatus.fromPairStatus` and `.fromQueueItem` only matched the
 * legacy spellings ("processing", "pending" as a *pair* status, "failed" as a
 * *pair* status), so a pair actually `transcribing` or `error` fell through to
 * `NotTranscribed`, and a queue item `in_progress` was neither `Transcribing`
 * nor counted by `TranscriptionRepository.activeQueueItemsFlow` — the item
 * currently being transcribed dropped out of the active set.
 */
class TranscriptionStatusMappingTest {

    // ---- fromPairStatus: real server values ------------------------------

    @Test
    fun `pair synced maps to Transcribed`() {
        assertEquals(TranscriptionStatus.Transcribed, TranscriptionStatus.fromPairStatus("synced", null))
    }

    @Test
    fun `pair transcribing maps to Transcribing with percent`() {
        assertEquals(
            TranscriptionStatus.Transcribing(42),
            TranscriptionStatus.fromPairStatus("transcribing", 0.42f),
        )
    }

    @Test
    fun `pair transcribing with no progress defaults to zero percent`() {
        assertEquals(
            TranscriptionStatus.Transcribing(0),
            TranscriptionStatus.fromPairStatus("transcribing", null),
        )
    }

    @Test
    fun `pair error maps to Failed`() {
        assertEquals(TranscriptionStatus.Failed(null), TranscriptionStatus.fromPairStatus("error", null))
    }

    @Test
    fun `pair unmatched maps to NotTranscribed`() {
        assertEquals(TranscriptionStatus.NotTranscribed, TranscriptionStatus.fromPairStatus("unmatched", null))
    }

    @Test
    fun `pair auto_matched maps to NotTranscribed`() {
        assertEquals(TranscriptionStatus.NotTranscribed, TranscriptionStatus.fromPairStatus("auto_matched", null))
    }

    @Test
    fun `pair manual_matched maps to NotTranscribed`() {
        assertEquals(TranscriptionStatus.NotTranscribed, TranscriptionStatus.fromPairStatus("manual_matched", null))
    }

    // ---- fromPairStatus: legacy aliases -----------------------------------

    @Test
    fun `legacy pair processing maps to Transcribing`() {
        assertEquals(
            TranscriptionStatus.Transcribing(50),
            TranscriptionStatus.fromPairStatus("processing", 0.5f),
        )
    }

    @Test
    fun `legacy pair pending maps to Queued zero`() {
        assertEquals(TranscriptionStatus.Queued(0), TranscriptionStatus.fromPairStatus("pending", null))
    }

    @Test
    fun `legacy pair failed maps to Failed`() {
        assertEquals(TranscriptionStatus.Failed(null), TranscriptionStatus.fromPairStatus("failed", null))
    }

    // ---- fromQueueItem: real server values --------------------------------

    private fun queueItem(
        status: String,
        progress: Float? = null,
        position: Int = 0,
        errorMessage: String? = null,
        message: String? = null,
    ) = QueueItemResponse(
        id = 1,
        book_pair_id = 7,
        status = status,
        priority = 0,
        position = position,
        progress = progress,
        message = message,
        error_message = errorMessage,
        created_at = "2026-01-01T00:00:00Z",
    )

    @Test
    fun `queue item in_progress maps to Transcribing with percent`() {
        assertEquals(
            TranscriptionStatus.Transcribing(42),
            TranscriptionStatus.fromQueueItem(queueItem(status = "in_progress", progress = 0.42f)),
        )
    }

    @Test
    fun `queue item completed maps to Transcribed`() {
        assertEquals(TranscriptionStatus.Transcribed, TranscriptionStatus.fromQueueItem(queueItem(status = "completed")))
    }

    @Test
    fun `queue item failed maps to Failed with error message`() {
        assertEquals(
            TranscriptionStatus.Failed("boom"),
            TranscriptionStatus.fromQueueItem(queueItem(status = "failed", errorMessage = "boom")),
        )
    }

    @Test
    fun `queue item cancelled falls back to message when no error message`() {
        assertEquals(
            TranscriptionStatus.Failed("cancelled by user"),
            TranscriptionStatus.fromQueueItem(queueItem(status = "cancelled", message = "cancelled by user")),
        )
    }

    @Test
    fun `queue item pending maps to Queued with position`() {
        assertEquals(
            TranscriptionStatus.Queued(3),
            TranscriptionStatus.fromQueueItem(queueItem(status = "pending", position = 3)),
        )
    }

    // ---- fromQueueItem: legacy alias ---------------------------------------

    @Test
    fun `legacy queue item processing maps to Transcribing`() {
        assertEquals(
            TranscriptionStatus.Transcribing(75),
            TranscriptionStatus.fromQueueItem(queueItem(status = "processing", progress = 0.75f)),
        )
    }

    // ---- activeQueueItemsFlow filter ---------------------------------------

    @Test
    fun `activeQueueItemsFlow keeps pending and in_progress but drops completed`() = runTest {
        val api = mockk<BookSyncApi>()
        coEvery { api.getTranscriptionQueue() } returns listOf(
            queueItem(status = "pending", position = 1),
            queueItem(status = "in_progress", progress = 0.1f),
            queueItem(status = "completed"),
        )
        val networkMonitor = mockk<NetworkMonitor>()
        every { networkMonitor.isOnline } returns MutableStateFlow(true)

        val bookPairDao = mockk<BookPairDao>(relaxed = true)
        val repository = TranscriptionRepository(api = api, networkMonitor = networkMonitor, bookPairDao = bookPairDao)
        val items = repository.activeQueueItemsFlow(intervalMs = 1_000_000L).first()

        assertEquals(listOf("pending", "in_progress"), items.map { it.status })
    }
}
