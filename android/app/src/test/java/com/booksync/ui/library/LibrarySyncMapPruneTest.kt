package com.booksync.ui.library

import android.content.Context
import androidx.work.WorkManager
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.remote.ServerUrlManager
import com.booksync.data.remote.TokenManager
import com.booksync.data.repository.BookSyncRepository
import com.booksync.data.repository.LastOpenedTimes
import com.booksync.data.repository.LibraryLoader
import com.booksync.data.repository.SyncMapInUse
import com.booksync.data.repository.SyncMapRemovalStore
import com.booksync.data.repository.TranscriptionRepository
import com.booksync.data.util.NetworkMonitor
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import io.mockk.mockkObject
import io.mockk.unmockkObject
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
 * The library-refresh sync-map prune (issue #678): [LibraryViewModel.refresh]
 * clears a cached map once nothing is downloaded and the pair is not
 * registered as open in [SyncMapInUse]. [SyncMapPruningTest] pins the pure
 * decision; this pins that [LibraryViewModel] actually calls it on refresh
 * and wires the real registry.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class LibrarySyncMapPruneTest {

    private val repository = mockk<BookSyncRepository>(relaxed = true)
    private val transcriptionRepository = mockk<TranscriptionRepository>(relaxed = true)
    private val workManager = mockk<WorkManager>(relaxed = true)
    private val syncMapRemovalStore = mockk<SyncMapRemovalStore>(relaxed = true)

    private fun pair(
        id: Int,
        ebookDownloaded: Boolean = false,
        audiobookDownloaded: Boolean = false,
        syncMapDownloaded: Boolean = false,
    ) = BookPairEntity(
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
        status = "synced",
        ebookDownloaded = ebookDownloaded,
        audiobookDownloaded = audiobookDownloaded,
        syncMapDownloaded = syncMapDownloaded,
    )

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        mockkObject(WorkManager.Companion)
        every { WorkManager.getInstance(any<Context>()) } returns workManager
        every { transcriptionRepository.activeQueueItemsFlow() } returns emptyFlow()
        every { repository.getRecentlyPlayedPairsFlow() } returns flowOf(emptyList())
        every { repository.lastOpenedTimesFlow() } returns flowOf(LastOpenedTimes())
        every { syncMapRemovalStore.removedIds() } returns flowOf(emptySet())
        SyncMapInUse.clearForTest()
    }

    @After
    fun tearDown() {
        unmockkObject(WorkManager.Companion)
        Dispatchers.resetMain()
        SyncMapInUse.clearForTest()
    }

    private fun newViewModel(pairs: List<BookPairEntity>): LibraryViewModel {
        every { repository.getPairsFlow() } returns flowOf(pairs)
        val loader = mockk<LibraryLoader>(relaxed = true)
        every { loader.refresh() } returns Job().apply { complete() }
        every { loader.lastError } returns MutableStateFlow(null)
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
            loader = loader,
            syncMapRemovalStore = syncMapRemovalStore,
        )
    }

    @Test
    fun `a streamed pair's cached map is cleared on refresh when not in use`() {
        // init fires refresh(silent = true) already.
        newViewModel(listOf(pair(id = 1, syncMapDownloaded = true)))

        coVerify(exactly = 1) { repository.clearSyncMapCache(1) }
    }

    @Test
    fun `a streamed pair's cached map is kept while registered in use`() {
        SyncMapInUse.register(1)

        newViewModel(listOf(pair(id = 1, syncMapDownloaded = true)))

        coVerify(exactly = 0) { repository.clearSyncMapCache(1) }
    }

    @Test
    fun `a downloaded pair's cached map is never pruned`() {
        newViewModel(listOf(pair(id = 1, ebookDownloaded = true, syncMapDownloaded = true)))

        coVerify(exactly = 0) { repository.clearSyncMapCache(1) }
    }

    @Test
    fun `a pair with no cached map is left alone`() {
        newViewModel(listOf(pair(id = 1, syncMapDownloaded = false)))

        coVerify(exactly = 0) { repository.clearSyncMapCache(any()) }
    }

    /**
     * The other half of issue #678's removal feature: a pair removed by hand
     * must not be re-fetched by the #537 sweep on the very next refresh.
     * [com.booksync.data.repository.SyncMapAutoFetchTest] pins the pure
     * decision; this pins that [LibraryViewModel] actually consults the store.
     */
    @Test
    fun `a pair removed by hand is not re-fetched by the sweep`() {
        every { syncMapRemovalStore.removedIds() } returns flowOf(setOf(1))
        val downloadedNotCached = pair(id = 1, ebookDownloaded = true, syncMapDownloaded = false)

        newViewModel(listOf(downloadedNotCached))

        io.mockk.verify(exactly = 0) {
            workManager.enqueueUniqueWork(
                "download_sync_1",
                any<androidx.work.ExistingWorkPolicy>(),
                any<androidx.work.OneTimeWorkRequest>(),
            )
        }
    }
}
