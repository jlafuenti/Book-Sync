package com.booksync.ui.player

import android.content.Context
import androidx.lifecycle.SavedStateHandle
import androidx.work.Data
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequest
import androidx.work.WorkInfo
import androidx.work.WorkManager
import androidx.work.workDataOf
import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.repository.BookSyncRepository
import com.booksync.worker.DownloadWorker
import io.mockk.coEvery
import io.mockk.every
import io.mockk.mockk
import io.mockk.mockkObject
import io.mockk.slot
import io.mockk.unmockkObject
import io.mockk.verify
import java.util.UUID
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
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test

/**
 * How the player screen drives and reads back a download (issue #217).
 *
 * The screen enqueues one unique request per book, then mirrors WorkManager's
 * state into two flows the composable draws from: a clamped percentage while
 * the worker runs, and the worker's own failure message — the one
 * `DownloadWorker` writes under `ERROR_KEY` — when it fails. A finished
 * standalone download re-reads the entity, because `isDownloaded` is what the
 * transport gating (`transportEnabled`) and the Cast offer key on.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class PlayerViewModelDownloadTest {

    private val dispatcher = StandardTestDispatcher()
    private val repository = mockk<BookSyncRepository>(relaxed = true)
    private val workManager = mockk<WorkManager>(relaxed = true)
    private val workInfos = MutableStateFlow<List<WorkInfo>>(emptyList())

    @Before
    fun setUp() {
        Dispatchers.setMain(dispatcher)
        mockkObject(WorkManager.Companion)
        every { WorkManager.getInstance(any<Context>()) } returns workManager
        every { workManager.getWorkInfosForUniqueWorkFlow(any()) } returns workInfos
        every { repository.getPairsFlow() } returns flowOf(listOf(pair()))
        coEvery { repository.getAudiobookById(17) } returns audio(downloaded = false)
        every { repository.localAudioFile(any()) } returns null
    }

    @After
    fun tearDown() {
        unmockkObject(WorkManager.Companion)
        Dispatchers.resetMain()
    }

    private fun pair(downloaded: Boolean = false) = BookPairEntity(
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

    private fun info(state: WorkInfo.State, progress: Data = Data.EMPTY, output: Data = Data.EMPTY) =
        WorkInfo(UUID.randomUUID(), state, setOf("download_worker"), output, progress)

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

    private fun enqueued(uniqueName: String): OneTimeWorkRequest {
        val request = slot<OneTimeWorkRequest>()
        verify(exactly = 1) {
            workManager.enqueueUniqueWork(uniqueName, ExistingWorkPolicy.REPLACE, capture(request))
        }
        return request.captured
    }

    // ---- paired ---------------------------------------------------------------

    @Test
    fun `downloadAudiobook enqueues one unique request for the pair's audio`() =
        runTest(dispatcher.scheduler) {
            val vm = pairedViewModel()
            advanceUntilIdle()

            vm.downloadAudiobook()
            advanceUntilIdle()

            val request = enqueued("download_audio_42")
            assertEquals("AUDIOBOOK", request.workSpec.input.getString(DownloadWorker.KEY_TYPE))
            assertEquals(42, request.workSpec.input.getInt(DownloadWorker.KEY_PAIR_ID, -1))
            assertEquals("the bar shows at once, before the worker's first report", 0, vm.downloadProgress.value)
        }

    @Test
    fun `progress from the worker is mirrored and clamped`() = runTest(dispatcher.scheduler) {
        val vm = pairedViewModel()
        advanceUntilIdle()
        vm.downloadAudiobook()
        advanceUntilIdle()

        workInfos.value = listOf(info(WorkInfo.State.RUNNING, workDataOf(DownloadWorker.PROGRESS_KEY to 42)))
        advanceUntilIdle()
        assertEquals(42, vm.downloadProgress.value)

        workInfos.value = listOf(info(WorkInfo.State.RUNNING, workDataOf(DownloadWorker.PROGRESS_KEY to 150)))
        advanceUntilIdle()
        assertEquals(100, vm.downloadProgress.value)

        workInfos.value = listOf(info(WorkInfo.State.RUNNING, workDataOf(DownloadWorker.PROGRESS_KEY to -5)))
        advanceUntilIdle()
        assertEquals(0, vm.downloadProgress.value)
    }

    @Test
    fun `success clears the progress`() = runTest(dispatcher.scheduler) {
        val vm = pairedViewModel()
        advanceUntilIdle()
        vm.downloadAudiobook()
        advanceUntilIdle()

        workInfos.value = listOf(info(WorkInfo.State.RUNNING, workDataOf(DownloadWorker.PROGRESS_KEY to 99)))
        advanceUntilIdle()
        workInfos.value = listOf(info(WorkInfo.State.SUCCEEDED))
        advanceUntilIdle()

        assertNull(vm.downloadProgress.value)
        assertNull(vm.downloadError.value)
    }

    @Test
    fun `failure surfaces the worker's own message and clears the progress`() = runTest(dispatcher.scheduler) {
        // The message is the one DownloadWorker writes for a book the server
        // no longer has (issue #338) — it must reach the screen verbatim
        // rather than being replaced by a generic "Download failed".
        val vm = pairedViewModel()
        advanceUntilIdle()
        vm.downloadAudiobook()
        advanceUntilIdle()

        workInfos.value = listOf(
            info(
                WorkInfo.State.FAILED,
                output = workDataOf(
                    DownloadWorker.ERROR_KEY to "That book is no longer in the library on the server.",
                ),
            )
        )
        advanceUntilIdle()

        assertEquals("That book is no longer in the library on the server.", vm.downloadError.value)
        assertNull(vm.downloadProgress.value)

        vm.clearDownloadError()
        assertNull(vm.downloadError.value)
    }

    @Test
    fun `a failure with no message still says something`() = runTest(dispatcher.scheduler) {
        val vm = pairedViewModel()
        advanceUntilIdle()
        vm.downloadAudiobook()
        advanceUntilIdle()

        workInfos.value = listOf(info(WorkInfo.State.FAILED))
        advanceUntilIdle()

        assertEquals("Download failed", vm.downloadError.value)
    }

    @Test
    fun `cancellation clears the progress without raising an error`() = runTest(dispatcher.scheduler) {
        val vm = pairedViewModel()
        advanceUntilIdle()
        vm.downloadAudiobook()
        advanceUntilIdle()

        workInfos.value = listOf(info(WorkInfo.State.CANCELLED))
        advanceUntilIdle()

        assertNull(vm.downloadProgress.value)
        assertNull(vm.downloadError.value)
    }

    @Test
    fun `a queued worker leaves the initial bar in place`() = runTest(dispatcher.scheduler) {
        val vm = pairedViewModel()
        advanceUntilIdle()
        vm.downloadAudiobook()
        advanceUntilIdle()

        workInfos.value = listOf(info(WorkInfo.State.ENQUEUED))
        advanceUntilIdle()

        assertEquals(0, vm.downloadProgress.value)
    }

    @Test
    fun `no pair loaded means nothing to download`() = runTest(dispatcher.scheduler) {
        every { repository.getPairsFlow() } returns flowOf(emptyList())
        val vm = pairedViewModel()
        advanceUntilIdle()

        vm.downloadAudiobook()
        advanceUntilIdle()

        verify(exactly = 0) {
            workManager.enqueueUniqueWork(any<String>(), any(), any<OneTimeWorkRequest>())
        }
        assertNull(vm.downloadProgress.value)
    }

    // ---- standalone -----------------------------------------------------------

    @Test
    fun `downloadStandaloneAudiobook enqueues the standalone request`() = runTest(dispatcher.scheduler) {
        val vm = standaloneViewModel()
        advanceUntilIdle()

        vm.downloadStandaloneAudiobook()
        advanceUntilIdle()

        val request = enqueued("download_standalone_audio_17")
        assertEquals("STANDALONE_AUDIOBOOK", request.workSpec.input.getString(DownloadWorker.KEY_TYPE))
        assertEquals(17, request.workSpec.input.getInt(DownloadWorker.KEY_PAIR_ID, -1))
        assertEquals(0, vm.downloadProgress.value)
    }

    @Test
    fun `a finished standalone download re-reads the entity so the screen knows the file is there`() =
        runTest(dispatcher.scheduler) {
            val vm = standaloneViewModel()
            advanceUntilIdle()
            vm.downloadStandaloneAudiobook()
            advanceUntilIdle()
            assertEquals(false, vm.standaloneAudio.value?.isDownloaded)

            coEvery { repository.getAudiobookById(17) } returns audio(downloaded = true)
            workInfos.value = listOf(info(WorkInfo.State.SUCCEEDED))
            advanceUntilIdle()

            assertEquals(true, vm.standaloneAudio.value?.isDownloaded)
            assertNull(vm.downloadProgress.value)
        }

    @Test
    fun `a failed standalone download reports the worker's message`() = runTest(dispatcher.scheduler) {
        val vm = standaloneViewModel()
        advanceUntilIdle()
        vm.downloadStandaloneAudiobook()
        advanceUntilIdle()

        workInfos.value = listOf(
            info(WorkInfo.State.FAILED, output = workDataOf(DownloadWorker.ERROR_KEY to "boom"))
        )
        advanceUntilIdle()

        assertEquals("boom", vm.downloadError.value)
        assertNull(vm.downloadProgress.value)
    }

    @Test
    fun `no standalone entity means nothing to download`() = runTest(dispatcher.scheduler) {
        coEvery { repository.getAudiobookById(17) } returns null
        val vm = standaloneViewModel()
        advanceUntilIdle()

        vm.downloadStandaloneAudiobook()
        advanceUntilIdle()

        verify(exactly = 0) {
            workManager.enqueueUniqueWork(any<String>(), any(), any<OneTimeWorkRequest>())
        }
    }
}
