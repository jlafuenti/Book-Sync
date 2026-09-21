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
 * The streamed-book sync-map prefetch sweep (issue #655 follow-up #2).
 *
 * The bug this pins: the sweep originally chose its candidate pairs from
 * `getRecentlyPlayedPairsFlow`, which only returns a pair once its bookmark's
 * `audioPositionMs` is already greater than zero. `audioPositionMs` only
 * becomes non-null via a sync-point match in `PositionRepository.saveReaderPosition`
 * (`resolvedAudioMs = syncPoint?.audioStartMs`), and a match requires the sync
 * map to already be cached. So a pair that has only ever been *read* — never
 * played — can never appear in that signal no matter how recently it was
 * opened: exactly the #643 scenario ("I was reading an ebook, then went to
 * the car and selected the same book"). The selection was circular and could
 * never reach the pairs it existed to reach.
 *
 * `lastOpenedTimesFlow` has no such gate — `saveReaderPosition` unconditionally
 * upserts a bookmark row regardless of whether a sync-point match was found,
 * so a pair the user has only read still shows up there, keyed by recency.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class LibrarySyncMapPrefetchTest {

    private val repository = mockk<BookSyncRepository>(relaxed = true)
    private val transcriptionRepository = mockk<TranscriptionRepository>(relaxed = true)
    private val workManager = mockk<WorkManager>(relaxed = true)

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
        // Read-but-never-played, throughout: no audio progress anywhere, on
        // any pair — the exact condition the bug depended on going unnoticed.
        every { repository.getRecentlyPlayedPairsFlow() } returns flowOf(emptyList())
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
        )
    }

    @Test
    fun `a pair that has only been read, never played, is still prefetched`() {
        every { repository.getPairsFlow() } returns flowOf(listOf(pair(id = 42)))
        // A reader position save wrote a bookmark row with no audio position —
        // the pair was opened recently, but only ever through the ebook.
        every { repository.lastOpenedTimesFlow() } returns flowOf(LastOpenedTimes(pairs = mapOf(42 to 1_000L)))

        newViewModel()

        verify(exactly = 1) {
            workManager.enqueueUniqueWork(
                "download_sync_42",
                ExistingWorkPolicy.KEEP,
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
    fun `a recently-opened pair that already has a current cached map is not re-fetched`() {
        every { repository.getPairsFlow() } returns flowOf(listOf(pair(id = 42, syncMapDownloaded = true)))
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
    fun `a recently-opened pair that is not yet synced is not prefetched`() {
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
    fun `only the most recently opened pairs up to the cap are prefetched`() {
        // STREAMING_PREFETCH_LIMIT is 10 — 11 candidates, the oldest must lose out.
        val pairs = (1..11).map { pair(id = it) }
        every { repository.getPairsFlow() } returns flowOf(pairs)
        // id 1 is opened longest ago (timestamp 1), id 11 most recently (timestamp 11).
        every { repository.lastOpenedTimesFlow() } returns
            flowOf(LastOpenedTimes(pairs = pairs.associate { it.id to it.id.toLong() }))

        newViewModel()

        verify(exactly = 0) {
            workManager.enqueueUniqueWork("download_sync_1", any<ExistingWorkPolicy>(), any<OneTimeWorkRequest>())
        }
        verify(exactly = 1) {
            workManager.enqueueUniqueWork("download_sync_11", ExistingWorkPolicy.KEEP, any<OneTimeWorkRequest>())
        }
    }
}
