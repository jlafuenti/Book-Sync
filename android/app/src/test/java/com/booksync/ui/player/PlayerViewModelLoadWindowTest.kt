package com.booksync.ui.player

import android.content.Context
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModelStore
import androidx.media3.common.Player
import androidx.media3.session.MediaController
import androidx.work.WorkManager
import com.booksync.SyncState
import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.BookmarkEntity
import com.booksync.data.local.entity.UserProgressEntity
import com.booksync.data.repository.BookSyncRepository
import com.booksync.data.repository.TEST_SCOPE
import io.mockk.coEvery
import io.mockk.every
import io.mockk.mockk
import io.mockk.mockkObject
import io.mockk.unmockkObject
import io.mockk.verify
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test

/**
 * Issue #706, the Android half of web #697: the window between opening a
 * streamed audiobook and the player reaching `STATE_READY`.
 *
 * The restore seek is parked in `pendingSeekPosition` until the player is
 * ready, and the controller reports `currentPosition == 0` meanwhile. The poll
 * loop used to copy that 0 into `positionMs` within half a second, so the
 * screen showed 0:00, a skip computed from 0, and leaving the screen made
 * `onCleared` save 0 — for a paired book that moved the ebook back to the start
 * as well. Until the parked seek lands, the saved position is the position.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class PlayerViewModelLoadWindowTest {

    private val dispatcher = StandardTestDispatcher()
    private val repository = mockk<BookSyncRepository>(relaxed = true)
    private val saved = 3_600_000

    @Before
    fun setUp() {
        Dispatchers.setMain(dispatcher)
        mockkObject(WorkManager.Companion)
        every { WorkManager.getInstance(any<Context>()) } returns mockk(relaxed = true)
        SyncState.pendingAudioSeekMs = -1L
        // Keeps loadAudio (and the android.net.Uri stubs behind it) out of it,
        // as in PlayerViewModelRestoreTest.
        every { repository.getPairsFlow() } returns emptyFlow()
        every { repository.getBookmarkFlow(42) } returns flowOf(
            BookmarkEntity(
                scopeKey = TEST_SCOPE, bookPairId = 42, source = "audiobook",
                epubChapter = 3, epubSentenceIndex = 1, audioPositionMs = saved, updatedAt = "1000",
            ),
        )
    }

    @After
    fun tearDown() {
        SyncState.pendingAudioSeekMs = -1L
        unmockkObject(WorkManager.Companion)
        Dispatchers.resetMain()
    }

    private fun viewModel(args: Map<String, Any>) = PlayerViewModel(
        repository = repository,
        appContext = mockk(relaxed = true),
        serverUrlManager = mockk(relaxed = true),
        coverArtHelper = mockk(relaxed = true),
        networkMonitor = mockk(relaxed = true),
        castSessionMonitor = mockk(relaxed = true),
        savedStateHandle = SavedStateHandle(args),
    )

    /** Still buffering a stream: nothing loaded, so the controller says 0. */
    private fun bufferingController(): MediaController =
        mockk<MediaController>(relaxed = true).also {
            every { it.isConnected } returns true
            every { it.playbackState } returns Player.STATE_BUFFERING
            every { it.currentPosition } returns 0L
            every { it.duration } returns 7_200_000L
            every { it.isPlaying } returns false
        }

    private val cleared = mutableSetOf<PlayerViewModel>()

    /** The real teardown path: ViewModelStore.clear() → onCleared(). Once per VM. */
    private fun clearViewModel(vm: PlayerViewModel) {
        if (!cleared.add(vm)) return
        val store = ViewModelStore()
        store.put("player", vm)
        store.clear()
    }

    /**
     * Runs [block] with the player in the load window: controller attached and
     * buffering, the restore seek parked, the poll loop running three ticks in.
     * Always tears the ViewModel down. The loop is `while (true) { delay(500) }`
     * on virtual time, so a failed assertion that skipped the teardown leaves
     * runTest's closing advanceUntilIdle spinning it until the heap runs out,
     * taking every later test with it (see PlayerViewModelHeartbeatTest).
     */
    private fun TestScope.inLoadWindow(
        args: Map<String, Any> = mapOf("pairId" to 42),
        block: TestScope.(PlayerViewModel, MediaController) -> Unit,
    ) {
        val ctrl = bufferingController()
        val vm = viewModel(args)
        vm.attachControllerForTest(ctrl)
        try {
            advanceUntilIdle()
            vm.startPositionPolling()
            advanceTimeBy(1_600) // three poll ticks
            block(vm, ctrl)
        } finally {
            clearViewModel(vm)
        }
    }

    @Test
    fun `the poll loop keeps the saved position while the stream loads`() = runTest(dispatcher.scheduler) {
        inLoadWindow { vm, _ ->
            assertEquals(saved.toLong(), vm.positionMs.value)
            assertEquals(true, vm.loading.value)
        }
    }

    @Test
    fun `leaving the screen during the load window saves the saved position, not 0`() =
        runTest(dispatcher.scheduler) {
            inLoadWindow { vm, _ -> clearViewModel(vm) }

            verify(exactly = 1) {
                repository.savePlaybackPositionDetached(
                    pairId = 42, audioPositionMs = saved, appendToLog = any(), claimFormat = any(),
                )
            }
            verify(exactly = 0) {
                repository.savePlaybackPositionDetached(any(), audioPositionMs = 0, any(), any())
            }
        }

    @Test
    fun `a standalone book left during the load window keeps its saved position`() =
        runTest(dispatcher.scheduler) {
            coEvery { repository.getAudiobookById(17) } returns AudioBookEntity(
                id = 17, title = "t", author = null, filename = "b.m4b", durationSeconds = 7200,
                format = "m4b", series = null, seriesIndex = null,
                uploadedAt = "2026-01-01T00:00:00", isDownloaded = false,
            )
            coEvery { repository.getProgressOnce("audiobook", 17) } returns UserProgressEntity(
                scopeKey = TEST_SCOPE, mediaType = "audiobook", mediaId = 17, bookPairId = null,
                epubCfi = null, epubChapter = null, epubProgressPercent = null,
                audioPositionMs = saved, isCompleted = false, updatedAt = 1_000L,
                deviceId = "web", capturedAt = "2026-08-20T10:00:00Z", syncedToServer = true,
            )

            inLoadWindow(mapOf("audiobookId" to 17)) { vm, _ ->
                assertEquals(saved.toLong(), vm.positionMs.value)
                clearViewModel(vm)
            }

            verify(exactly = 1) {
                repository.savePlaybackPositionStandaloneDetached(
                    audiobookId = 17, audioPositionMs = saved, claimFormat = any(),
                )
            }
        }

    @Test
    fun `skips, chapter jumps and scrubbing are ignored until the player is ready`() =
        runTest(dispatcher.scheduler) {
            inLoadWindow { vm, ctrl ->
                vm.skipForward()
                vm.skipBackward()
                vm.seekTo(120_000L)
                vm.skipToPreviousChapter()
                advanceTimeBy(600)

                verify(exactly = 0) { ctrl.seekTo(any<Long>()) }
                assertEquals(saved.toLong(), vm.positionMs.value)
            }
        }

    @Test
    fun `play pressed during the load window is queued, not refused`() = runTest(dispatcher.scheduler) {
        inLoadWindow { vm, ctrl ->
            vm.togglePlayback()

            verify(exactly = 1) { ctrl.play() }
        }
    }

    @Test
    fun `once ready the parked seek lands and the loop mirrors the player again`() =
        runTest(dispatcher.scheduler) {
            inLoadWindow { vm, ctrl ->
                every { ctrl.playbackState } returns Player.STATE_READY
                vm.onPlayerStateChanged(ctrl, Player.STATE_READY)
                verify { ctrl.seekTo(saved.toLong()) }
                assertEquals(false, vm.loading.value)

                every { ctrl.currentPosition } returns saved + 1_500L
                advanceTimeBy(600)
                assertEquals(saved + 1_500L, vm.positionMs.value)

                vm.skipForward()
                verify { ctrl.seekTo(saved + 1_500L + 30_000L) }
            }
        }
}
