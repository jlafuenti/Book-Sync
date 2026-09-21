package com.booksync.ui.library

import android.content.Context
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
 * "Remove sync data" from Library's card menu (issue #678): clears the cache
 * now and marks the pair so the #537 sweep does not immediately re-fetch it.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class LibraryRemoveSyncDataTest {

    private val repository = mockk<BookSyncRepository>(relaxed = true)
    private val transcriptionRepository = mockk<TranscriptionRepository>(relaxed = true)
    private val syncMapRemovalStore = mockk<SyncMapRemovalStore>(relaxed = true)

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        mockkObject(WorkManager.Companion)
        every { WorkManager.getInstance(any<Context>()) } returns mockk(relaxed = true)
        every { transcriptionRepository.activeQueueItemsFlow() } returns emptyFlow()
        every { repository.getPairsFlow() } returns flowOf(emptyList())
        every { repository.lastOpenedTimesFlow() } returns flowOf(LastOpenedTimes())
        every { syncMapRemovalStore.removedIds() } returns flowOf(emptySet())
    }

    @After
    fun tearDown() {
        unmockkObject(WorkManager.Companion)
        Dispatchers.resetMain()
    }

    private fun newViewModel(): LibraryViewModel {
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
