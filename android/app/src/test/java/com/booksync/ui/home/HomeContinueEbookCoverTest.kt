package com.booksync.ui.home

import android.content.Context
import androidx.work.WorkManager
import com.booksync.data.local.entity.EBookEntity
import com.booksync.data.remote.ServerUrlManager
import com.booksync.data.repository.BookSyncRepository
import com.booksync.data.repository.TranscriptionRepository
import com.booksync.data.util.NetworkMonitor
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
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test

/**
 * A standalone ebook in Home's "Continue Reading" row showed the placeholder:
 * [HomeItem] carried only an audiobook cover, and the EBOOK card got none.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class HomeContinueEbookCoverTest {

    private val repository = mockk<BookSyncRepository>(relaxed = true)
    private val transcriptionRepository = mockk<TranscriptionRepository>(relaxed = true)

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        mockkObject(WorkManager.Companion)
        every { WorkManager.getInstance(any<Context>()) } returns mockk(relaxed = true)
        every { transcriptionRepository.activeQueueItemsFlow() } returns emptyFlow()
        every { repository.getRecentlyPlayedPairsFlow() } returns flowOf(emptyList())
        every { repository.getRecentlyPlayedStandaloneAudiobooksFlow() } returns flowOf(emptyList())
    }

    @After
    fun tearDown() {
        unmockkObject(WorkManager.Companion)
        Dispatchers.resetMain()
    }

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
            serverVersionGate = com.booksync.data.remote.ServerVersionGate(
                mockk(relaxed = true),
                serverUrlManager,
            ),
            context = mockk(relaxed = true),
        )
    }

    @Test
    fun `a recently read standalone ebook carries its cover`() {
        val ebook = EBookEntity(
            id = 71, title = "Standalone", author = null, filename = "s.epub", fileSize = null,
            format = "epub", series = null, seriesIndex = null, uploadedAt = "2026-01-01T00:00:00",
            isDownloaded = true, coverFilename = "/api/files/covers/ebook_71.jpg",
        )
        every { repository.getRecentlyReadEbooksFlow() } returns flowOf(listOf(ebook))

        val item = newViewModel().continueItems.value.single()

        assertEquals(HomeItem.MediaType.EBOOK, item.mediaType)
        assertEquals("/api/files/covers/ebook_71.jpg", item.ebookCoverPath)
    }
}
