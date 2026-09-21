package com.booksync.ui.downloaded

import android.content.Context
import androidx.work.WorkManager
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.remote.ServerUrlManager
import com.booksync.data.repository.BookSyncRepository
import com.booksync.data.repository.SyncMapRemovalStore
import com.booksync.data.util.NetworkMonitor
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import io.mockk.mockkObject
import io.mockk.unmockkObject
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Before
import org.junit.Test

/**
 * "Remove sync data" from the Downloaded tab's card menu (issue #678) — same
 * action as `LibraryViewModel.removeSyncData`: clear the cache now, and mark
 * the pair so the #537 sweep does not immediately re-fetch it.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class DownloadedViewModelRemoveSyncDataTest {

    private val repository = mockk<BookSyncRepository>(relaxed = true)
    private val syncMapRemovalStore = mockk<SyncMapRemovalStore>(relaxed = true)

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        mockkObject(WorkManager.Companion)
        val workManager = mockk<WorkManager>(relaxed = true)
        every { workManager.getWorkInfosByTagFlow(any()) } returns emptyFlow()
        every { WorkManager.getInstance(any<Context>()) } returns workManager
    }

    @After
    fun tearDown() {
        unmockkObject(WorkManager.Companion)
        Dispatchers.resetMain()
    }

    private fun newViewModel(): DownloadedViewModel {
        val serverUrlManager = mockk<ServerUrlManager>()
        every { serverUrlManager.currentUrl } returns "https://tandem.example.com"
        val networkMonitor = mockk<NetworkMonitor>()
        every { networkMonitor.isOnline } returns MutableStateFlow(true)
        return DownloadedViewModel(
            repository = repository,
            networkMonitor = networkMonitor,
            serverUrlManager = serverUrlManager,
            tokenManager = mockk(relaxed = true),
            context = mockk(relaxed = true),
            syncMapRemovalStore = syncMapRemovalStore,
        )
    }

    private fun pair() = BookPairEntity(
        id = 7,
        ebookId = 100, ebookTitle = "E", ebookAuthor = null, ebookFilename = "e.epub", ebookFormat = "epub",
        audiobookId = 200, audiobookTitle = "A", audiobookAuthor = null, audiobookFilename = "a.m4b",
        audiobookFormat = "m4b", audiobookDurationSeconds = null, status = "synced",
    )

    @Test
    fun `removeSyncData clears the cache and marks the pair removed`() {
        newViewModel().removeSyncData(pair())

        coVerify(exactly = 1) { repository.clearSyncMapCache(7) }
        coVerify(exactly = 1) { syncMapRemovalStore.markRemoved(7) }
    }
}
