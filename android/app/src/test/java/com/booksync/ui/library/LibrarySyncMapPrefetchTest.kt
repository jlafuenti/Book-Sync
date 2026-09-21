package com.booksync.ui.library

import android.content.Context
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequest
import androidx.work.WorkManager
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.remote.ServerUrlManager
import com.booksync.data.remote.TokenManager
import com.booksync.data.repository.BookSyncRepository
import com.booksync.data.repository.LastOpenedTimes
import com.booksync.data.repository.LibraryLoader
import com.booksync.data.repository.SyncMapRemovalStore
import com.booksync.data.repository.TranscriptionRepository
import com.booksync.data.util.NetworkMonitor
import io.mockk.every
import io.mockk.mockk
import io.mockk.mockkObject
import io.mockk.unmockkObject
import io.mockk.verify
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Before
import org.junit.Test

/**
 * The streamed-book sync-map prefetch sweep (#655 follow-up #2) is gone
 * (issue #678): the owner's decision is that a pair keeps a cached sync map
 * only while it is downloaded, or open right now — no more prefetch for a
 * book that is merely streamed. This inverts what
 * `fetchSyncMapsForStreamedRecentlyOpened`'s tests used to pin: the exact
 * "read but never played, opened recently" pair that function existed to
 * catch must now never be enqueued, because it has nothing downloaded.
 *
 * `fetchMissingSyncMaps` (the #537 sweep, download-gated) is untouched and
 * still covered by `SyncMapAutoFetchTest`.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class LibrarySyncMapPrefetchTest {

    private val repository = mockk<BookSyncRepository>(relaxed = true)
    private val transcriptionRepository = mockk<TranscriptionRepository>(relaxed = true)
    private val workManager = mockk<WorkManager>(relaxed = true)
    private val syncMapRemovalStore = mockk<SyncMapRemovalStore>(relaxed = true)

    private fun pair(id: Int, status: String = "synced", syncMapDownloaded: Boolean = false) = BookPairEntity(
        id = id,
        ebookId = 7,
        ebookTitle = "Axis Test",
        ebookAuthor = "Author",
        ebookFilename = "axis.epub",
        ebookFormat = "epub",
        audiobookId = 9,
        audiobookTitle = "Axis Test",
        audiobookAuthor = "Author",
        audiobookFilename = "axis.m4b",
        audiobookFormat = "m4b",
        audiobookDurationSeconds = 1_000,
        status = status,
        // Deliberately nothing downloaded on any of these fixtures — every
        // test here is about the streamed (not-downloaded) case.
        ebookDownloaded = false,
        audiobookDownloaded = false,
        syncMapDownloaded = syncMapDownloaded,
    )

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        // WorkManager.getInstance is a Kotlin companion function — same idiom
        // as SearchDownloadTest/LibraryRefreshMessageTest.
        mockkObject(WorkManager.Companion)
        every { WorkManager.getInstance(any<Context>()) } returns workManager
        every { transcriptionRepository.activeQueueItemsFlow() } returns emptyFlow()
        every { repository.getRecentlyPlayedPairsFlow() } returns flowOf(emptyList())
        every { syncMapRemovalStore.removedIds() } returns flowOf(emptySet())
    }

    @After
    fun tearDown() {
        unmockkObject(WorkManager.Companion)
        Dispatchers.resetMain()
    }

    private fun newLoader(): LibraryLoader {
        val loader = mockk<LibraryLoader>(relaxed = true)
        every { loader.refresh() } returns Job().apply { complete() }
        every { loader.lastError } returns MutableStateFlow(null)
        return loader
    }

    /** [LibraryViewModel.init] fires one `refresh(silent = true)` already —
     *  callers stub the repository first, then construct, then verify. */
    private fun newViewModel(): LibraryViewModel {
        val networkMonitor = mockk<NetworkMonitor>(relaxed = true)
        every { networkMonitor.isOnline } returns MutableStateFlow(true)
        val serverUrlManager = mockk<ServerUrlManager>(relaxed = true)
        every { serverUrlManager.currentUrl } returns "https://tandem.example.com"
        val tokenManager = mockk<TokenManager>(relaxed = true)
        every { tokenManager.getRole() } returns flowOf("editor")
        return LibraryViewModel(
            repository = repository,
            transcriptionRepository = transcriptionRepository,
            networkMonitor = networkMonitor,
            serverUrlManager = serverUrlManager,
            tokenManager = tokenManager,
            context = mockk(relaxed = true),
            loader = newLoader(),
            syncMapRemovalStore = syncMapRemovalStore,
        )
    }

    @Test
    fun `a recently opened, not-downloaded pair is never enqueued`() {
        // This is exactly the case the removed sweep existed to catch — a
        // pair read recently but never downloaded. It must now stay untouched.
        every { repository.getPairsFlow() } returns flowOf(listOf(pair(id = 42)))
        every { repository.lastOpenedTimesFlow() } returns flowOf(LastOpenedTimes(pairs = mapOf(42 to 1_000L)))

        newViewModel()

        verify(exactly = 0) {
            workManager.enqueueUniqueWork(
                "download_sync_42",
                any<ExistingWorkPolicy>(),
                any<OneTimeWorkRequest>(),
            )
        }
    }

    @Test
    fun `a recently opened, not-downloaded, unsynced pair is also never enqueued`() {
        every { repository.getPairsFlow() } returns flowOf(listOf(pair(id = 42, status = "transcribing")))
        every { repository.lastOpenedTimesFlow() } returns flowOf(LastOpenedTimes(pairs = mapOf(42 to 1_000L)))

        newViewModel()

        verify(exactly = 0) {
            workManager.enqueueUniqueWork(
                "download_sync_42",
                any<ExistingWorkPolicy>(),
                any<OneTimeWorkRequest>(),
            )
        }
    }

    @Test
    fun `a pair with no bookmark activity anywhere is not prefetched`() {
        every { repository.getPairsFlow() } returns flowOf(listOf(pair(id = 42)))
        every { repository.lastOpenedTimesFlow() } returns flowOf(LastOpenedTimes())

        newViewModel()

        verify(exactly = 0) {
            workManager.enqueueUniqueWork(
                "download_sync_42",
                any<ExistingWorkPolicy>(),
                any<OneTimeWorkRequest>(),
            )
        }
    }

    @Test
    fun `many recently-opened streamed pairs are still never enqueued, regardless of recency`() {
        // The old sweep capped candidates at 10 and picked the most recent —
        // there is no such cap to test any more because there is no sweep;
        // every one of these, downloaded by none of them, must stay untouched.
        val pairs = (1..11).map { pair(id = it) }
        every { repository.getPairsFlow() } returns flowOf(pairs)
        every { repository.lastOpenedTimesFlow() } returns
            flowOf(LastOpenedTimes(pairs = pairs.associate { it.id to it.id.toLong() }))

        newViewModel()

        verify(exactly = 0) {
            workManager.enqueueUniqueWork(any<String>(), any<ExistingWorkPolicy>(), any<OneTimeWorkRequest>())
        }
    }
}
