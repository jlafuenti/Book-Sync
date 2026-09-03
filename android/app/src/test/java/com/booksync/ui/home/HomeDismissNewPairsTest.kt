package com.booksync.ui.home

import android.content.Context
import androidx.work.WorkManager
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.remote.ServerUrlManager
import com.booksync.data.repository.BookSyncRepository
import com.booksync.data.repository.TranscriptionRepository
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
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Before
import org.junit.Test

/**
 * Issue #222: Home's "New Pairs" section had no dismiss of its own — the only
 * way to clear it was Library → NEW → "Acknowledge all", which also cleared
 * every new ebook and audiobook. The section header's Dismiss must acknowledge
 * **pairs only**.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class HomeDismissNewPairsTest {

    private val repository = mockk<BookSyncRepository>(relaxed = true)
    private val transcriptionRepository = mockk<TranscriptionRepository>(relaxed = true)

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        // WorkManager.getInstance is a Kotlin companion function, so the
        // companion object (not a Java static) is what needs mocking.
        mockkObject(WorkManager.Companion)
        every { WorkManager.getInstance(any<Context>()) } returns mockk(relaxed = true)
        every { transcriptionRepository.activeQueueItemsFlow() } returns emptyFlow()
    }

    @After
    fun tearDown() {
        unmockkObject(WorkManager.Companion)
        Dispatchers.resetMain()
    }

    private fun pair(id: Int) = BookPairEntity(
        id = id,
        ebookId = id, ebookTitle = "E$id", ebookAuthor = null, ebookFilename = "e$id.epub",
        ebookFormat = "epub",
        audiobookId = id, audiobookTitle = "A$id", audiobookAuthor = null,
        audiobookFilename = "a$id.m4b", audiobookFormat = "m4b",
        audiobookDurationSeconds = null,
        status = "synced",
    )

    private fun newViewModel(): HomeViewModel {
        val serverUrlManager = mockk<ServerUrlManager>(relaxed = true)
        every { serverUrlManager.currentUrl } returns "https://tandem.example.com"
        every { serverUrlManager.serverUrlFlow } returns emptyFlow()
        val networkMonitor = mockk<NetworkMonitor>(relaxed = true)
        every { networkMonitor.isOnline } returns MutableStateFlow(true)
        return HomeViewModel(
            repository = repository,
            transcriptionRepository = transcriptionRepository,
            networkMonitor = networkMonitor,
            serverUrlManager = serverUrlManager,
            context = mockk(relaxed = true),
        )
    }

    @Test
    fun `dismissNewPairs acknowledges every new pair and nothing else`() {
        every { repository.getNewPairsFlow() } returns flowOf(listOf(pair(1), pair(2)))

        newViewModel().dismissNewPairs()

        coVerify(exactly = 1) { repository.acknowledgeItems(listOf(1, 2), "pair") }
        coVerify(exactly = 0) { repository.acknowledgeItems(any(), "ebook") }
        coVerify(exactly = 0) { repository.acknowledgeItems(any(), "audiobook") }
    }

    @Test
    fun `dismissNewPairs with nothing new does not call the repository`() {
        every { repository.getNewPairsFlow() } returns flowOf(emptyList())

        newViewModel().dismissNewPairs()

        coVerify(exactly = 0) { repository.acknowledgeItems(any(), any()) }
    }
}
