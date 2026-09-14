package com.booksync.ui.components

import com.booksync.data.remote.TokenManager
import com.booksync.data.remote.dto.QueueItemResponse
import com.booksync.data.remote.dto.TranscriptionStatus
import com.booksync.data.repository.TranscriptionRepository
import com.booksync.data.util.NetworkMonitor
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * The state machine behind the "not synced yet" dialog (issue #536), gating
 * every pair open (Home/Library/Downloaded/Book Details -> reader/player) and
 * the player's "Switch to Reader" button.
 *
 * [PairOpenGateViewModel.requestOpen] proceeds immediately when
 * [TranscriptionRepository.readiness] says the pair is ready (null); otherwise
 * it publishes a [PendingOpen] and withholds [proceed] until the user
 * explicitly confirms via [PairOpenGateViewModel.continueAnyway].
 */
@OptIn(ExperimentalCoroutinesApi::class)
class PairOpenGateTest {

    private val transcriptionRepository = mockk<TranscriptionRepository>()
    private val networkMonitor = mockk<NetworkMonitor>()

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        every { networkMonitor.isOnline } returns MutableStateFlow(true)
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    private fun newViewModel(role: String? = "editor"): PairOpenGateViewModel {
        val tokenManager = mockk<TokenManager>()
        every { tokenManager.getRole() } returns flowOf(role)
        return PairOpenGateViewModel(
            transcriptionRepository = transcriptionRepository,
            networkMonitor = networkMonitor,
            tokenManager = tokenManager,
        )
    }

    private fun queueItem(status: String = "pending", position: Int = 1) = QueueItemResponse(
        id = 1,
        book_pair_id = 42,
        status = status,
        priority = 0,
        position = position,
        created_at = "2026-01-01T00:00:00Z",
    )

    @Test
    fun `ready pair proceeds immediately and pending stays null`() {
        coEvery { transcriptionRepository.readiness(42) } returns null
        val viewModel = newViewModel()

        var proceedCount = 0
        viewModel.requestOpen(42, "Open anyway") { proceedCount++ }

        assertEquals(1, proceedCount)
        assertNull(viewModel.pending.value)
    }

    @Test
    fun `not ready pair publishes pending and does not proceed`() {
        coEvery { transcriptionRepository.readiness(42) } returns TranscriptionStatus.NotTranscribed
        val viewModel = newViewModel()

        var proceedCount = 0
        viewModel.requestOpen(42, "Open anyway") { proceedCount++ }

        assertEquals(0, proceedCount)
        val pending = viewModel.pending.value
        assertEquals(42, pending?.pairId)
        assertEquals(TranscriptionStatus.NotTranscribed, pending?.status)
        assertEquals("Open anyway", pending?.continueLabel)
    }

    @Test
    fun `continueAnyway runs proceed exactly once and clears pending`() {
        coEvery { transcriptionRepository.readiness(42) } returns TranscriptionStatus.Queued(2)
        val viewModel = newViewModel()

        var proceedCount = 0
        viewModel.requestOpen(42, "Open anyway") { proceedCount++ }
        viewModel.continueAnyway()

        assertEquals(1, proceedCount)
        assertNull(viewModel.pending.value)

        // A second call with nothing pending must not run proceed again.
        viewModel.continueAnyway()
        assertEquals(1, proceedCount)
    }

    @Test
    fun `dismiss clears pending without ever proceeding`() {
        coEvery { transcriptionRepository.readiness(42) } returns TranscriptionStatus.Transcribing(50)
        val viewModel = newViewModel()

        var proceedCount = 0
        viewModel.requestOpen(42, "Open anyway") { proceedCount++ }
        viewModel.dismiss()

        assertEquals(0, proceedCount)
        assertNull(viewModel.pending.value)
    }

    @Test
    fun `transcribe success adds to queue, sets message, and refreshes status`() {
        coEvery { transcriptionRepository.readiness(42) } returnsMany listOf(
            TranscriptionStatus.NotTranscribed,
            TranscriptionStatus.Queued(1),
        )
        coEvery { transcriptionRepository.addToQueue(42) } returns Result.success(queueItem())
        val viewModel = newViewModel()

        viewModel.requestOpen(42, "Open anyway") {}
        viewModel.transcribe()

        coVerify { transcriptionRepository.addToQueue(42) }
        assertEquals("Added to transcription queue", viewModel.message.value)
        assertEquals(TranscriptionStatus.Queued(1), viewModel.pending.value?.status)
    }

    @Test
    fun `transcribe failure sets a message and keeps pending as-is`() {
        coEvery { transcriptionRepository.readiness(42) } returns TranscriptionStatus.NotTranscribed
        coEvery { transcriptionRepository.addToQueue(42) } returns
            Result.failure(TranscriptionRepository.OfflineException())
        val viewModel = newViewModel()

        viewModel.requestOpen(42, "Open anyway") {}
        viewModel.transcribe()

        assertEquals("Device is offline", viewModel.message.value)
        assertEquals(TranscriptionStatus.NotTranscribed, viewModel.pending.value?.status)
        assertEquals(42, viewModel.pending.value?.pairId)
    }

    @Test
    fun `clearMessage resets the one-shot message`() {
        coEvery { transcriptionRepository.readiness(42) } returns TranscriptionStatus.NotTranscribed
        coEvery { transcriptionRepository.addToQueue(42) } returns Result.success(queueItem())
        val viewModel = newViewModel()

        viewModel.requestOpen(42, "Open anyway") {}
        viewModel.transcribe()
        assertEquals("Added to transcription queue", viewModel.message.value)

        viewModel.clearMessage()
        assertNull(viewModel.message.value)
    }

    @Test
    fun `role user cannot transcribe`() {
        assertFalse(newViewModel(role = "user").canTranscribe.value)
    }

    @Test
    fun `role editor can transcribe`() {
        assertTrue(newViewModel(role = "editor").canTranscribe.value)
    }

    @Test
    fun `role admin can transcribe`() {
        assertTrue(newViewModel(role = "admin").canTranscribe.value)
    }
}
