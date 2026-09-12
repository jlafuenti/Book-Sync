package com.booksync.ui.library

import android.content.Context
import androidx.work.Data
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequest
import androidx.work.WorkInfo
import androidx.work.WorkManager
import androidx.work.workDataOf
import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.local.entity.EBookEntity
import com.booksync.data.repository.BookSyncRepository
import com.booksync.worker.DownloadWorker
import io.mockk.coEvery
import io.mockk.coVerify
import com.booksync.data.util.NetworkMonitor
import io.mockk.every
import io.mockk.mockk
import io.mockk.mockkObject
import io.mockk.unmockkObject
import io.mockk.verify
import java.net.UnknownHostException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * "Download" on a search result must work on a cold cache, and must never fail
 * in silence (issue #338).
 *
 * On a fresh install the Library tab may never have been opened, so a result
 * the server returned names an id Room has never seen. The tap enqueued
 * `DownloadWorker` regardless; the worker read Room, found nothing, and
 * returned `Result.failure("Audiobook not found in DB")` — no notification, no
 * toast, no error on the card. The user's only clue was logcat.
 *
 * Two halves are pinned here: the row is resolved (and cached) before any work
 * is enqueued, and every way that can fail ends in a message on screen.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class SearchDownloadTest {

    private val repository = mockk<BookSyncRepository>(relaxed = true)
    private val workManager = mockk<WorkManager>(relaxed = true)
    private val workInfos = MutableStateFlow<List<WorkInfo>>(emptyList())

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        // WorkManager.getInstance is a Kotlin companion function, so the
        // companion object (not a Java static) is what needs mocking.
        mockkObject(WorkManager.Companion)
        every { workManager.getWorkInfosByTagFlow(any()) } returns workInfos
        every { WorkManager.getInstance(any<Context>()) } returns workManager
    }

    @After
    fun tearDown() {
        unmockkObject(WorkManager.Companion)
        Dispatchers.resetMain()
    }

    private fun newViewModel(): SearchViewModel {
        val tokenManager = mockk<com.booksync.data.remote.TokenManager>(relaxed = true)
        every { tokenManager.getRole() } returns flowOf("admin")
        val networkMonitor = mockk<NetworkMonitor>()
        every { networkMonitor.isOnline } returns MutableStateFlow(true)
        return SearchViewModel(
            repository = repository,
            networkMonitor = networkMonitor,
            tokenManager = tokenManager,
            context = mockk(relaxed = true),
        )
    }

    private val audiobookResult = SearchResultItem(
        id = "audio_1516", title = "Bartleby", author = "H. M.",
        series = null, seriesIndex = null, isEbook = false, isAudiobook = true,
    )
    private val ebookResult = SearchResultItem(
        id = "ebook_42", title = "Bartleby", author = "H. M.",
        series = null, seriesIndex = null, isEbook = true, isAudiobook = false,
    )
    private val pairResult = SearchResultItem(
        id = "pair_7", title = "Bartleby", author = "H. M.",
        series = null, seriesIndex = null, isEbook = true, isAudiobook = true,
        pairId = 7,
    )

    private val audiobook = AudioBookEntity(
        id = 1516, title = "Bartleby", author = null, filename = "b.m4b",
        durationSeconds = 60, format = "m4b", series = null, seriesIndex = null,
        uploadedAt = "2026-01-01T00:00:00",
    )
    private val ebook = EBookEntity(
        id = 42, title = "Bartleby", author = null, filename = "b.epub",
        fileSize = 1, format = "epub", series = null, seriesIndex = null,
        uploadedAt = "2026-01-01T00:00:00",
    )
    private val pair = BookPairEntity(
        id = 7, ebookId = 42, ebookTitle = "Bartleby", ebookAuthor = null,
        ebookFilename = "b.epub", ebookFormat = "epub",
        audiobookId = 1516, audiobookTitle = "Bartleby", audiobookAuthor = null,
        audiobookFilename = "b.m4b", audiobookFormat = "m4b",
        audiobookDurationSeconds = 60, status = "synced",
    )

    // --- The happy path a cold cache used to fail --------------------------

    @Test
    fun `an audiobook missing from the cache is resolved before work is enqueued`() {
        coEvery { repository.resolveAudiobookById(1516) } returns audiobook

        newViewModel().downloadFromResult(audiobookResult)

        coVerify(exactly = 1) { repository.resolveAudiobookById(1516) }
        verify(exactly = 1) {
            workManager.enqueueUniqueWork(
                "download_search_standalone_audiobook_1516",
                ExistingWorkPolicy.REPLACE,
                any<OneTimeWorkRequest>(),
            )
        }
    }

    @Test
    fun `an ebook result resolves the ebook`() {
        coEvery { repository.resolveEbookById(42) } returns ebook

        newViewModel().downloadFromResult(ebookResult)

        coVerify(exactly = 1) { repository.resolveEbookById(42) }
        verify(exactly = 1) {
            workManager.enqueueUniqueWork(
                "download_search_standalone_ebook_42",
                ExistingWorkPolicy.REPLACE,
                any<OneTimeWorkRequest>(),
            )
        }
    }

    @Test
    fun `a pair result resolves the pair`() {
        coEvery { repository.resolvePairById(7) } returns pair

        newViewModel().downloadFromResult(pairResult)

        coVerify(exactly = 1) { repository.resolvePairById(7) }
        verify(exactly = 1) {
            workManager.enqueueUniqueWork(
                "download_search_all_7",
                ExistingWorkPolicy.REPLACE,
                any<OneTimeWorkRequest>(),
            )
        }
    }

    // --- Failures the user can see -----------------------------------------

    @Test
    fun `an unreachable server shows an error and enqueues nothing`() {
        coEvery { repository.resolveAudiobookById(1516) } throws UnknownHostException("no such host")

        val vm = newViewModel()
        vm.downloadFromResult(audiobookResult)

        val message = vm.downloadError.value
        assertNotNull("a failed tap must not be silent", message)
        assertTrue("must blame the network, was: $message", message!!.contains("reach the server"))
        verify(exactly = 0) {
            workManager.enqueueUniqueWork(any<String>(), any<ExistingWorkPolicy>(), any<OneTimeWorkRequest>())
        }
    }

    @Test
    fun `a book the server no longer has shows an error and enqueues nothing`() {
        coEvery { repository.resolveAudiobookById(1516) } returns null

        val vm = newViewModel()
        vm.downloadFromResult(audiobookResult)

        val message = vm.downloadError.value
        assertNotNull("a failed tap must not be silent", message)
        assertTrue("must name the book, was: $message", message!!.contains("Bartleby"))
        verify(exactly = 0) {
            workManager.enqueueUniqueWork(any<String>(), any<ExistingWorkPolicy>(), any<OneTimeWorkRequest>())
        }
    }

    @Test
    fun `a worker that fails later still surfaces its reason`() {
        // The resolve can succeed and the download still fail — a 500 halfway
        // through, say. Library and the player both toast the worker's error;
        // Search observed nothing at all.
        coEvery { repository.resolveAudiobookById(1516) } returns audiobook
        val vm = newViewModel()
        vm.downloadFromResult(audiobookResult)

        workInfos.value = listOf(failedWorkInfo(workDataOf(DownloadWorker.ERROR_KEY to "HTTP 500")))

        assertTrue(
            "the worker's reason must reach the screen, was: ${vm.downloadError.value}",
            vm.downloadError.value.orEmpty().contains("HTTP 500"),
        )
    }

    @Test
    fun `dismissing the error clears it`() {
        coEvery { repository.resolveAudiobookById(1516) } returns null
        val vm = newViewModel()
        vm.downloadFromResult(audiobookResult)
        assertNotNull(vm.downloadError.value)

        vm.clearDownloadError()

        assertNull(vm.downloadError.value)
    }

    @Test
    fun `a result with no numeric id is a no-op`() {
        val vm = newViewModel()

        vm.downloadFromResult(audiobookResult.copy(id = "audio_nonsense"))

        assertEquals(null, vm.downloadError.value)
        verify(exactly = 0) {
            workManager.enqueueUniqueWork(any<String>(), any<ExistingWorkPolicy>(), any<OneTimeWorkRequest>())
        }
    }

    private fun failedWorkInfo(output: Data): WorkInfo {
        val info = mockk<WorkInfo>(relaxed = true)
        every { info.state } returns WorkInfo.State.FAILED
        every { info.outputData } returns output
        return info
    }
}
