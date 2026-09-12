package com.booksync.ui.player

import android.content.Context
import androidx.lifecycle.SavedStateHandle
import androidx.media3.common.PlaybackException
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequest
import androidx.work.WorkManager
import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.repository.BookSyncRepository
import com.booksync.player.PlaybackRecovery
import io.mockk.coEvery
import io.mockk.coVerifyOrder
import io.mockk.every
import io.mockk.mockk
import io.mockk.mockkObject
import io.mockk.unmockkObject
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test

/**
 * The player screen's half of issue #475: a failure the listener can see.
 *
 * The mapping from an error code to a message and an offer is
 * `PlaybackFailureTest`'s subject. What this covers is the state the screen
 * draws from — that a reported failure reaches it at all, that it carries the
 * right remedy for *this* book (the same malformed-container error means
 * "download it again" for a book on the phone and "try again" for one being
 * streamed), and that it goes away when sound arrives, so a banner cannot
 * outlive the failure it describes.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class PlayerViewModelPlaybackErrorTest {

    private val dispatcher = StandardTestDispatcher()
    private val repository = mockk<BookSyncRepository>(relaxed = true)
    private val workManager = mockk<WorkManager>(relaxed = true)

    @Before
    fun setUp() {
        Dispatchers.setMain(dispatcher)
        mockkObject(WorkManager.Companion)
        every { WorkManager.getInstance(any<Context>()) } returns workManager
        every { workManager.getWorkInfosForUniqueWorkFlow(any()) } returns MutableStateFlow(emptyList())
        every { repository.localAudioFile(any()) } returns null
    }

    @After
    fun tearDown() {
        unmockkObject(WorkManager.Companion)
        Dispatchers.resetMain()
    }

    private fun pair(downloaded: Boolean) = BookPairEntity(
        id = 42, ebookId = 7, ebookTitle = "The Mad Ship", ebookAuthor = "Robin Hobb",
        ebookFilename = "mad-ship.epub", ebookFormat = "epub",
        audiobookId = 9, audiobookTitle = "The Mad Ship", audiobookAuthor = "Robin Hobb",
        audiobookFilename = "mad-ship.m4b", audiobookFormat = "m4b",
        audiobookDurationSeconds = 7200, status = "synced",
        audiobookDownloaded = downloaded,
    )

    private fun audio(downloaded: Boolean) = AudioBookEntity(
        id = 17, title = "A Standalone Audiobook", author = null, filename = "book.m4b",
        durationSeconds = 7200, format = "m4b", series = null, seriesIndex = null,
        uploadedAt = "2026-01-01T00:00:00", isDownloaded = downloaded,
    )

    private fun pairedViewModel(downloaded: Boolean): PlayerViewModel {
        every { repository.getPairsFlow() } returns flowOf(listOf(pair(downloaded)))
        return PlayerViewModel(
            repository = repository,
            appContext = mockk(relaxed = true),
            serverUrlManager = mockk(relaxed = true),
            coverArtHelper = mockk(relaxed = true),
            networkMonitor = mockk(relaxed = true),
            castSessionMonitor = mockk(relaxed = true),
            savedStateHandle = SavedStateHandle(mapOf("pairId" to 42)),
        )
    }

    private fun standaloneViewModel(downloaded: Boolean): PlayerViewModel {
        every { repository.getPairsFlow() } returns flowOf(emptyList())
        coEvery { repository.getAudiobookById(17) } returns audio(downloaded)
        return PlayerViewModel(
            repository = repository,
            appContext = mockk(relaxed = true),
            serverUrlManager = mockk(relaxed = true),
            coverArtHelper = mockk(relaxed = true),
            networkMonitor = mockk(relaxed = true),
            castSessionMonitor = mockk(relaxed = true),
            savedStateHandle = SavedStateHandle(mapOf("audiobookId" to 17)),
        )
    }

    @Test
    fun `nothing is shown until something fails`() = runTest(dispatcher.scheduler) {
        val vm = pairedViewModel(downloaded = true)
        advanceUntilIdle()

        assertNull(vm.playbackError.value)
    }

    @Test
    fun `a reported failure reaches the screen`() = runTest(dispatcher.scheduler) {
        val vm = pairedViewModel(downloaded = true)
        advanceUntilIdle()

        vm.onPlaybackError(PlaybackException.ERROR_CODE_DECODER_INIT_FAILED)

        val failure = vm.playbackError.value
        assertNotNull("the failure the player reported must not stop here", failure)
        assertEquals(PlaybackRecovery.NONE, failure!!.recovery)
    }

    /**
     * The remedy has to be read off this book, not off the error alone — which
     * is the reason the ViewModel owns this step rather than the listener
     * calling the mapper directly.
     */
    @Test
    fun `a downloaded book with unreadable bytes is offered the download again`() =
        runTest(dispatcher.scheduler) {
            val vm = pairedViewModel(downloaded = true)
            advanceUntilIdle()

            vm.onPlaybackError(PlaybackException.ERROR_CODE_PARSING_CONTAINER_MALFORMED)

            assertEquals(PlaybackRecovery.REDOWNLOAD, vm.playbackError.value?.recovery)
        }

    @Test
    fun `the same failure while streaming is offered a retry instead`() =
        runTest(dispatcher.scheduler) {
            val vm = pairedViewModel(downloaded = false)
            advanceUntilIdle()

            vm.onPlaybackError(PlaybackException.ERROR_CODE_PARSING_CONTAINER_MALFORMED)

            assertEquals(PlaybackRecovery.RETRY, vm.playbackError.value?.recovery)
        }

    @Test
    fun `a standalone audiobook reports its own download state, not the pair's`() =
        runTest(dispatcher.scheduler) {
            val vm = standaloneViewModel(downloaded = true)
            advanceUntilIdle()

            vm.onPlaybackError(PlaybackException.ERROR_CODE_PARSING_CONTAINER_MALFORMED)

            assertEquals(PlaybackRecovery.REDOWNLOAD, vm.playbackError.value?.recovery)
        }

    /**
     * The banner must not outlive the failure. Sound arriving is the proof that
     * whatever it said is no longer true — including after a retry the listener
     * started from the banner itself.
     */
    @Test
    fun `playback starting clears the failure`() = runTest(dispatcher.scheduler) {
        val vm = pairedViewModel(downloaded = true)
        advanceUntilIdle()
        vm.onPlaybackError(PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_FAILED)
        assertNotNull(vm.playbackError.value)

        vm.onPlayingChanged(true)

        assertNull(vm.playbackError.value)
        assertEquals(true, vm.isPlaying.value)
    }

    /**
     * A pause is not a recovery. Stopping playback while the message is up must
     * leave it up — this is the path that would quietly re-hide the failure,
     * because `onIsPlayingChanged(false)` fires when a failed player gives up.
     */
    @Test
    fun `playback stopping leaves the failure on screen`() = runTest(dispatcher.scheduler) {
        val vm = pairedViewModel(downloaded = true)
        advanceUntilIdle()
        vm.onPlaybackError(PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_FAILED)

        vm.onPlayingChanged(false)

        assertNotNull(vm.playbackError.value)
        assertEquals(false, vm.isPlaying.value)
    }

    /**
     * The offer has to actually work. `DownloadWorker` skips a book whose entity
     * already says `isDownloaded` — so enqueuing a plain download over a broken
     * copy finishes instantly, changes nothing, and leaves the listener tapping
     * a button that succeeds and fixes nothing. Caught on an emulator, against a
     * downloaded file overwritten with random bytes: the work ran, reported
     * SUCCESS, and the same 200 000 corrupt bytes were still on disk.
     *
     * Deleting first is what makes the re-download a re-download.
     */
    @Test
    fun `re-downloading deletes the broken copy first, or the worker skips it`() =
        runTest(dispatcher.scheduler) {
            val vm = standaloneViewModel(downloaded = true)
            advanceUntilIdle()
            vm.onPlaybackError(PlaybackException.ERROR_CODE_PARSING_CONTAINER_UNSUPPORTED)

            vm.redownloadAudiobook()
            advanceUntilIdle()

            coVerifyOrder {
                repository.deleteStandaloneAudiobook(any())
                workManager.enqueueUniqueWork("download_standalone_audio_17", any<ExistingWorkPolicy>(), any<OneTimeWorkRequest>())
            }
            assertNull("the banner goes once the remedy is under way", vm.playbackError.value)
        }

    @Test
    fun `a paired book's re-download deletes the pair's audio copy`() =
        runTest(dispatcher.scheduler) {
            val vm = pairedViewModel(downloaded = true)
            advanceUntilIdle()
            vm.onPlaybackError(PlaybackException.ERROR_CODE_PARSING_CONTAINER_UNSUPPORTED)

            vm.redownloadAudiobook()
            advanceUntilIdle()

            coVerifyOrder {
                repository.deleteAudiobook(any())
                workManager.enqueueUniqueWork("download_audio_42", any<ExistingWorkPolicy>(), any<OneTimeWorkRequest>())
            }
        }

    @Test
    fun `dismissing the message clears it`() = runTest(dispatcher.scheduler) {
        val vm = pairedViewModel(downloaded = true)
        advanceUntilIdle()
        vm.onPlaybackError(PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_FAILED)

        vm.clearPlaybackError()

        assertNull(vm.playbackError.value)
    }

    /**
     * Retrying takes the message down before it asks the player again: if the
     * attempt fails, `onPlayerError` puts it straight back, and if the controller
     * is gone there is nothing for a stale banner to describe.
     */
    @Test
    fun `retrying takes the message down`() = runTest(dispatcher.scheduler) {
        val vm = pairedViewModel(downloaded = true)
        advanceUntilIdle()
        vm.onPlaybackError(PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_FAILED)

        vm.retryPlayback()

        assertNull(vm.playbackError.value)
    }
}
