package com.booksync.ui.player

import android.content.Context
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModelStore
import androidx.media3.common.C
import androidx.media3.common.Player
import androidx.media3.session.MediaController
import androidx.work.WorkManager
import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.remote.BookmarkLogResponse
import com.booksync.data.repository.BookSyncRepository
import com.booksync.player.AudioPlayerService
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import io.mockk.mockkObject
import io.mockk.unmockkObject
import io.mockk.verify
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.StandardTestDispatcher
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
 * The player screen's 500 ms poll loop and what it may and may not do
 * (issues #217, #226).
 *
 * The loop mirrors the controller into the screen's state — position,
 * duration, playing — and requests the chapter list once the player is ready.
 * It writes **no** position and sends **no** pause command: AudioPlayerService
 * owns the heartbeat, the seek flush and the pause boundary, and this loop used
 * to detect the pause edge and save again, which doubled every write. That rule
 * was pinned by reading the source (`PauseOwnershipWiringTest`); this runs the
 * loop on virtual time and asserts it.
 *
 * What the loop *does* feed is teardown: `onCleared`'s one remaining save
 * claims the format only if the player was playing at that moment, and the
 * loop is where `isPlaying` comes from.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class PlayerViewModelHeartbeatTest {

    private val dispatcher = StandardTestDispatcher()
    private val repository = mockk<BookSyncRepository>(relaxed = true)

    @Before
    fun setUp() {
        Dispatchers.setMain(dispatcher)
        mockkObject(WorkManager.Companion)
        every { WorkManager.getInstance(any<Context>()) } returns mockk(relaxed = true)
        every { repository.getPairsFlow() } returns flowOf(listOf(pair()))
        every { repository.localAudioFile(any()) } returns null
    }

    @After
    fun tearDown() {
        unmockkObject(WorkManager.Companion)
        Dispatchers.resetMain()
    }

    private fun pair() = BookPairEntity(
        id = 42, ebookId = 7, ebookTitle = "The Mad Ship", ebookAuthor = "Robin Hobb",
        ebookFilename = "mad-ship.epub", ebookFormat = "epub",
        audiobookId = 9, audiobookTitle = "The Mad Ship", audiobookAuthor = "Robin Hobb",
        audiobookFilename = "mad-ship.m4b", audiobookFormat = "m4b",
        audiobookDurationSeconds = 7200, status = "synced", audiobookDownloaded = true,
    )

    private fun pairedViewModel() = PlayerViewModel(
        repository = repository,
        appContext = mockk(relaxed = true),
        serverUrlManager = mockk(relaxed = true),
        coverArtHelper = mockk(relaxed = true),
        networkMonitor = mockk(relaxed = true),
        castSessionMonitor = mockk(relaxed = true),
        savedStateHandle = SavedStateHandle(mapOf("pairId" to 42)),
    )

    private fun standaloneViewModel() = PlayerViewModel(
        repository = repository,
        appContext = mockk(relaxed = true),
        serverUrlManager = mockk(relaxed = true),
        coverArtHelper = mockk(relaxed = true),
        networkMonitor = mockk(relaxed = true),
        castSessionMonitor = mockk(relaxed = true),
        savedStateHandle = SavedStateHandle(mapOf("audiobookId" to 17)),
    )

    private fun controller(
        position: Long = 90_000L,
        duration: Long = 7_200_000L,
        playing: Boolean = true,
        state: Int = Player.STATE_READY,
    ): MediaController = mockk<MediaController>(relaxed = true).also {
        every { it.isConnected } returns true
        every { it.currentPosition } returns position
        every { it.duration } returns duration
        every { it.isPlaying } returns playing
        every { it.playbackState } returns state
    }

    /**
     * The real teardown path: ViewModelStore.clear() → ViewModel.clear() → onCleared().
     *
     * Every test that starts the poll loop must end through here. The loop is
     * `while (true) { delay(500) … }` on virtual time, so `runTest`'s closing
     * `advanceUntilIdle()` would otherwise spin it forever — and mockk records
     * each `ctrl.currentPosition` call, so "forever" ends in an
     * OutOfMemoryError that takes every later test class in the JVM with it.
     */
    private fun clearViewModel(vm: PlayerViewModel) {
        val store = ViewModelStore()
        store.put("player", vm)
        store.clear()
    }

    private fun verifyNoPositionWriteFromTheViewModel() {
        verify(exactly = 0) { repository.savePlaybackPositionDetached(any(), any(), any(), any()) }
        verify(exactly = 0) { repository.savePlaybackPositionStandaloneDetached(any(), any(), any()) }
    }

    // ---- the loop -------------------------------------------------------------

    @Test
    fun `the poll loop mirrors the controller into the screen state`() = runTest(dispatcher.scheduler) {
        val ctrl = controller()
        val vm = pairedViewModel()
        advanceUntilIdle()
        vm.attachControllerForTest(ctrl)

        vm.startPositionPolling()
        advanceTimeBy(1_600) // three ticks

        assertEquals(90_000L, vm.positionMs.value)
        assertEquals(true, vm.isPlaying.value)
        assertEquals(7_200_000L, vm.durationMs.value)
        clearViewModel(vm)
    }

    @Test
    fun `an unknown controller duration does not shrink the one the library knows`() =
        runTest(dispatcher.scheduler) {
            // Before the item is prepared the controller reports C.TIME_UNSET;
            // the pair row already said 2 h and the slider must not collapse.
            val ctrl = controller(duration = C.TIME_UNSET, state = Player.STATE_BUFFERING)
            val vm = pairedViewModel()
            advanceUntilIdle()
            vm.attachControllerForTest(ctrl)

            vm.startPositionPolling()
            advanceTimeBy(1_100)

            assertEquals(7_200_000L, vm.durationMs.value)
            clearViewModel(vm)
        }

    @Test
    fun `the loop writes no position and announces no pause of its own`() = runTest(dispatcher.scheduler) {
        // A pause edge seen by the loop — the double-write of issue #226.
        val ctrl = controller()
        every { ctrl.isPlaying } returnsMany listOf(true, true, false)
        val vm = pairedViewModel()
        advanceUntilIdle()
        vm.attachControllerForTest(ctrl)

        vm.startPositionPolling()
        advanceTimeBy(2_600) // five ticks: two playing, then paused

        assertEquals(false, vm.isPlaying.value)
        verifyNoPositionWriteFromTheViewModel()
        verify(exactly = 0) {
            ctrl.sendCustomCommand(
                match { it.customAction == AudioPlayerService.CMD_USER_PAUSE },
                any(),
            )
        }
        clearViewModel(vm)
    }

    @Test
    fun `chapters are requested once the player is ready, and only once`() = runTest(dispatcher.scheduler) {
        val ctrl = controller()
        val vm = pairedViewModel()
        advanceUntilIdle()
        vm.attachControllerForTest(ctrl)

        vm.startPositionPolling()
        advanceTimeBy(2_100) // four ticks, all ready

        verify(exactly = 1) {
            ctrl.sendCustomCommand(
                match { it.customAction == AudioPlayerService.CMD_GET_CHAPTERS },
                any(),
            )
        }
        clearViewModel(vm)
    }

    @Test
    fun `a disconnected controller is left alone`() = runTest(dispatcher.scheduler) {
        val ctrl = controller()
        every { ctrl.isConnected } returns false
        val vm = pairedViewModel()
        advanceUntilIdle()
        vm.attachControllerForTest(ctrl)

        vm.startPositionPolling()
        advanceTimeBy(1_100)

        assertEquals(0L, vm.positionMs.value)
        verify(exactly = 0) { ctrl.currentPosition }
        clearViewModel(vm)
    }

    // ---- teardown: the one write this ViewModel still makes ---------------------

    @Test
    fun `teardown while playing persists the polled position and claims the format`() =
        runTest(dispatcher.scheduler) {
            val ctrl = controller(playing = true)
            val vm = pairedViewModel()
            advanceUntilIdle()
            vm.attachControllerForTest(ctrl)
            vm.startPositionPolling()
            advanceTimeBy(600)

            clearViewModel(vm)

            verify(exactly = 1) {
                repository.savePlaybackPositionDetached(
                    pairId = 42, audioPositionMs = 90_000, appendToLog = false, claimFormat = true,
                )
            }
            verify(exactly = 1) { ctrl.release() }
        }

    @Test
    fun `teardown while paused persists the position without claiming the format`() =
        runTest(dispatcher.scheduler) {
            // Contract § "Who may claim source": a teardown while paused is a
            // background save — it must not re-route the next open to the
            // player after the user has moved on to the reader.
            val ctrl = controller(playing = false)
            val vm = pairedViewModel()
            advanceUntilIdle()
            vm.attachControllerForTest(ctrl)
            vm.startPositionPolling()
            advanceTimeBy(600)

            clearViewModel(vm)

            verify(exactly = 1) {
                repository.savePlaybackPositionDetached(
                    pairId = 42, audioPositionMs = 90_000, appendToLog = false, claimFormat = false,
                )
            }
        }

    // ---- the other controller commands ---------------------------------------

    @Test
    fun `cycleSpeed walks the options, wraps, and tells the service each time`() =
        runTest(dispatcher.scheduler) {
            val ctrl = controller()
            val vm = pairedViewModel()
            advanceUntilIdle()
            vm.attachControllerForTest(ctrl)

            val seen = mutableListOf<Float>()
            repeat(PlayerViewModel.SPEED_OPTIONS.size) {
                vm.cycleSpeed()
                seen += vm.speed.value
            }

            assertEquals(listOf(1.25f, 1.5f, 2.0f, 0.5f, 0.75f, 1.0f), seen)
            verify(exactly = PlayerViewModel.SPEED_OPTIONS.size) {
                ctrl.sendCustomCommand(
                    match { it.customAction == AudioPlayerService.CMD_SET_SPEED },
                    any(),
                )
            }
        }

    @Test
    fun `the sleep timer counts down on the clock and clears itself`() = runTest(dispatcher.scheduler) {
        val ctrl = controller()
        val vm = pairedViewModel()
        advanceUntilIdle()
        vm.attachControllerForTest(ctrl)

        vm.setSleepTimer(1)

        assertEquals(1, vm.sleepTimerMinutes.value)
        assertEquals(60_000L, vm.sleepTimerRemainingMs.value)
        verify(exactly = 1) {
            ctrl.sendCustomCommand(
                match { it.customAction == AudioPlayerService.CMD_SET_SLEEP_TIMER },
                any(),
            )
        }

        advanceTimeBy(30_500)
        assertEquals(30_000L, vm.sleepTimerRemainingMs.value)

        advanceTimeBy(31_000)
        assertEquals(0L, vm.sleepTimerRemainingMs.value)
        assertEquals("the timer clears itself when it runs out", 0, vm.sleepTimerMinutes.value)
    }

    @Test
    fun `cancelling the sleep timer zeroes it and tells the service`() = runTest(dispatcher.scheduler) {
        val ctrl = controller()
        val vm = pairedViewModel()
        advanceUntilIdle()
        vm.attachControllerForTest(ctrl)
        vm.setSleepTimer(15)

        vm.cancelSleepTimer()

        assertEquals(0, vm.sleepTimerMinutes.value)
        assertEquals(0L, vm.sleepTimerRemainingMs.value)
        verify(exactly = 2) {
            ctrl.sendCustomCommand(
                match { it.customAction == AudioPlayerService.CMD_SET_SLEEP_TIMER },
                any(),
            )
        }
    }

    // ---- actions that route by mode ------------------------------------------

    @Test
    fun `markComplete and resetProgress act on the pair`() = runTest(dispatcher.scheduler) {
        val vm = pairedViewModel()
        advanceUntilIdle()

        vm.markComplete()
        vm.resetProgress()
        advanceUntilIdle()

        coVerify(exactly = 1) { repository.markPairComplete(42, 7, 9) }
        coVerify(exactly = 1) { repository.resetPairProgress(42) }
        coVerify(exactly = 0) { repository.markComplete(any(), any()) }
        coVerify(exactly = 0) { repository.resetStandaloneProgress(any(), any()) }
    }

    @Test
    fun `markComplete and resetProgress act on the standalone audiobook`() = runTest(dispatcher.scheduler) {
        coEvery { repository.getAudiobookById(17) } returns AudioBookEntity(
            id = 17, title = "t", author = null, filename = "b.m4b", durationSeconds = 7200,
            format = "m4b", series = null, seriesIndex = null,
            uploadedAt = "2026-01-01T00:00:00", isDownloaded = true,
        )
        val vm = standaloneViewModel()
        advanceUntilIdle()

        vm.markComplete()
        vm.resetProgress()
        advanceUntilIdle()

        coVerify(exactly = 1) { repository.markComplete("audiobook", 17) }
        coVerify(exactly = 1) { repository.resetStandaloneProgress("audiobook", 17) }
        coVerify(exactly = 0) { repository.markPairComplete(any(), any(), any()) }
        coVerify(exactly = 0) { repository.resetPairProgress(any()) }
    }

    @Test
    fun `loadHistory exposes the pair's bookmark history`() = runTest(dispatcher.scheduler) {
        val entry = BookmarkLogResponse(id = 1, source = "ebook", changed_at = "2026-01-01T00:00:00")
        coEvery { repository.getBookmarkHistory(42, any()) } returns listOf(entry)
        val vm = pairedViewModel()
        advanceUntilIdle()

        vm.loadHistory()
        advanceUntilIdle()

        assertEquals(listOf(entry), vm.history.value)
    }
}
