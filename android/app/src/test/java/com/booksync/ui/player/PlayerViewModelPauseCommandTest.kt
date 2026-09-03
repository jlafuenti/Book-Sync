package com.booksync.ui.player

import android.content.Context
import androidx.lifecycle.SavedStateHandle
import androidx.media3.session.MediaController
import androidx.work.WorkManager
import com.booksync.data.repository.BookSyncRepository
import com.booksync.player.AudioPlayerService
import io.mockk.every
import io.mockk.mockk
import io.mockk.mockkObject
import io.mockk.unmockkObject
import io.mockk.verify
import io.mockk.verifyOrder
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Before
import org.junit.Test

/**
 * Issue #226: the service is the sole owner of playback-position writes.
 *
 * Pausing from this screen used to produce TWO `PUT /api/sync/position/...`
 * calls within ~500 ms — `AudioPlayerService.onIsPlayingChanged(false)` plus
 * this ViewModel's 500 ms poll loop, which saved again purely so the phone
 * screen could claim the format. `stopAndSave` made it three.
 *
 * The screen now contributes only the intent the service cannot infer: it
 * sends `CMD_USER_PAUSE` before `pause()`, and the service's listener does the
 * one write with `claimFormat = true`. So the ViewModel must issue the command
 * and then pause, and must not save anything itself.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class PlayerViewModelPauseCommandTest {

    private val dispatcher = StandardTestDispatcher()
    private val repository = mockk<BookSyncRepository>(relaxed = true)

    @Before
    fun setUp() {
        Dispatchers.setMain(dispatcher)
        mockkObject(WorkManager.Companion)
        every { WorkManager.getInstance(any<Context>()) } returns mockk(relaxed = true)
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
        savedStateHandle = SavedStateHandle(mapOf("pairId" to 42)),
    )

    private fun playingController(): MediaController {
        val ctrl = mockk<MediaController>(relaxed = true)
        every { ctrl.isPlaying } returns true
        return ctrl
    }

    private fun verifyNoPositionWriteFromTheViewModel() {
        verify(exactly = 0) {
            repository.savePlaybackPositionDetached(any(), any(), any(), any())
        }
        verify(exactly = 0) {
            repository.savePlaybackPositionStandaloneDetached(any(), any(), any())
        }
    }

    @Test
    fun `togglePlayback while playing announces the pause and then pauses`() =
        runTest(dispatcher.scheduler) {
            val ctrl = playingController()
            val vm = viewModel()
            advanceUntilIdle()
            vm.attachControllerForTest(ctrl)

            vm.togglePlayback()

            verifyOrder {
                ctrl.sendCustomCommand(
                    match { it.customAction == AudioPlayerService.CMD_USER_PAUSE },
                    any(),
                )
                ctrl.pause()
            }
        }

    @Test
    fun `togglePlayback does not write the position itself — the service owns that`() =
        runTest(dispatcher.scheduler) {
            val ctrl = playingController()
            val vm = viewModel()
            advanceUntilIdle()
            vm.attachControllerForTest(ctrl)

            vm.togglePlayback()
            advanceUntilIdle()

            verifyNoPositionWriteFromTheViewModel()
        }

    @Test
    fun `stopAndSave announces the pause and leaves the write to the service`() =
        runTest(dispatcher.scheduler) {
            // "Switch to reader" was the three-write case: the service
            // listener, the poll loop, and stopAndSave's own saveBookmark.
            val ctrl = playingController()
            val vm = viewModel()
            advanceUntilIdle()
            vm.attachControllerForTest(ctrl)

            vm.stopAndSave()
            advanceUntilIdle()

            verifyOrder {
                ctrl.sendCustomCommand(
                    match { it.customAction == AudioPlayerService.CMD_USER_PAUSE },
                    any(),
                )
                ctrl.pause()
            }
            verifyNoPositionWriteFromTheViewModel()
        }

    @Test
    fun `togglePlayback while paused plays without announcing a pause`() =
        runTest(dispatcher.scheduler) {
            val ctrl = mockk<MediaController>(relaxed = true)
            every { ctrl.isPlaying } returns false
            val vm = viewModel()
            advanceUntilIdle()
            vm.attachControllerForTest(ctrl)

            vm.togglePlayback()

            verify { ctrl.play() }
            verify(exactly = 0) {
                ctrl.sendCustomCommand(
                    match { it.customAction == AudioPlayerService.CMD_USER_PAUSE },
                    any(),
                )
            }
        }
}
