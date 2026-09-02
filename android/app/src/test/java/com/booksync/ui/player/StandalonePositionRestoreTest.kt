package com.booksync.ui.player

import com.booksync.data.repository.TEST_SCOPE

import android.content.Context
import androidx.lifecycle.SavedStateHandle
import androidx.work.WorkManager
import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.UserProgressEntity
import com.booksync.data.repository.BookSyncRepository
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import io.mockk.mockkObject
import io.mockk.unmockkObject
import io.mockk.verify
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test

/**
 * Regression tests for issue #142: the standalone branch of PlayerViewModel's
 * init never restored the saved position — the player opened at 0:00, and the
 * pause/teardown saves then overwrote the user's real position on the server
 * with a fresh captured_at. This is exactly the failure
 * docs/position-sync-contract.md exists to prevent ("Resume paths refresh
 * first"; "a record holding any anchor never resolves to start of book").
 */
@OptIn(ExperimentalCoroutinesApi::class)
class StandalonePositionRestoreTest {

    private val dispatcher = StandardTestDispatcher()
    private val repository = mockk<BookSyncRepository>(relaxed = true)

    private val audio = AudioBookEntity(
        id = 17,
        title = "A Standalone Audiobook",
        author = "Author",
        filename = "book.m4b",
        durationSeconds = 7200,
        format = "m4b",
        series = null,
        seriesIndex = null,
        uploadedAt = "2026-01-01T00:00:00",
        isDownloaded = true,
    )

    private val progress = UserProgressEntity(scopeKey = TEST_SCOPE, 
        mediaType = "audiobook",
        mediaId = 17,
        bookPairId = null,
        epubCfi = null,
        epubChapter = null,
        epubProgressPercent = null,
        audioPositionMs = 3_600_000,
        isCompleted = false,
        updatedAt = 1_000L,
        deviceId = "web",
        capturedAt = "2026-08-20T10:00:00Z",
        syncedToServer = true,
    )

    @Before
    fun setUp() {
        Dispatchers.setMain(dispatcher)
        mockkObject(WorkManager.Companion)
        every { WorkManager.getInstance(any<Context>()) } returns mockk(relaxed = true)
        coEvery { repository.getAudiobookById(17) } returns audio
        coEvery { repository.getProgressOnce("audiobook", 17) } returns progress
    }

    @After
    fun tearDown() {
        unmockkObject(WorkManager.Companion)
        Dispatchers.resetMain()
    }

    private fun viewModel() = PlayerViewModel(
        repository = repository,
        appContext = mockk(relaxed = true),
        serverUrlManager = mockk(relaxed = true),
            coverArtHelper = mockk(relaxed = true),
        savedStateHandle = SavedStateHandle(mapOf("audiobookId" to 17)),
    )

    @Test
    fun `pulls the server position and restores the local row before playback`() = runTest(dispatcher.scheduler) {
        val vm = viewModel()
        advanceUntilIdle()

        // Contract § "Resume paths refresh first": the bounded server pull
        // must happen, and the position must come up at the saved point —
        // not 0:00.
        coVerify(exactly = 1) { repository.refreshProgress("audiobook", 17) }
        assertEquals(3_600_000L, vm.positionMs.value)
    }

    @Test
    fun `restores from the local cache when the server refresh hangs`() = runTest(dispatcher.scheduler) {
        // The pull is bounded (SERVER_POSITION_TIMEOUT_MS): an unreachable
        // server must not stall the restore, and must not lose it either.
        coEvery { repository.refreshProgress("audiobook", 17) } coAnswers { awaitCancellation() }

        val vm = viewModel()
        advanceUntilIdle()

        assertEquals(3_600_000L, vm.positionMs.value)
    }

    @Test
    fun `does not save a standalone position before the restore has been attempted`() = runTest(dispatcher.scheduler) {
        // The write gate: a save issued before the restore landed would write
        // ~0 with a fresh captured_at and destroy the real position.
        coEvery { repository.refreshProgress("audiobook", 17) } coAnswers { awaitCancellation() }

        val vm = viewModel()
        dispatcher.scheduler.runCurrent() // entity loaded; restore still parked in the refresh

        vm.stopAndSave()
        dispatcher.scheduler.runCurrent()

        verify(exactly = 0) {
            repository.savePlaybackPositionStandaloneDetached(any(), any(), any())
        }

        // Once the restore completes (bounded timeout elapses), the gate opens.
        advanceUntilIdle()
        vm.stopAndSave()
        advanceUntilIdle()

        verify(exactly = 1) {
            repository.savePlaybackPositionStandaloneDetached(any(), any(), any())
        }
    }
}
