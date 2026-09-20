package com.booksync.ui.home

import android.content.Context
import androidx.work.WorkManager
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.remote.ServerUrlManager
import com.booksync.data.remote.dto.QueueItemResponse
import com.booksync.data.repository.BookSyncRepository
import com.booksync.data.repository.LibraryLoadState
import com.booksync.data.repository.LibraryLoader
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
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test

/**
 * Issue #549: every card in Home's "In Queue" row showed the placeholder cover.
 *
 * The queue endpoint returns only `book_pair_id` and `book_title`, and the row was
 * built from that alone — so it had nothing to resolve a cover from, while the very
 * same pair, already in Room with its `audiobookCoverPath`, showed its cover in
 * "Recently Added" directly above. The queue items now borrow cover and author from
 * the cached pair.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class HomeQueueCoversTest {

    private val repository = mockk<BookSyncRepository>(relaxed = true)
    private val transcriptionRepository = mockk<TranscriptionRepository>(relaxed = true)

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        mockkObject(WorkManager.Companion)
        every { WorkManager.getInstance(any<Context>()) } returns mockk(relaxed = true)
    }

    @After
    fun tearDown() {
        unmockkObject(WorkManager.Companion)
        Dispatchers.resetMain()
    }

    private fun pair(id: Int, coverPath: String?, ebookAuthor: String?, audiobookAuthor: String?) =
        BookPairEntity(
            id = id,
            ebookId = id, ebookTitle = "E$id", ebookAuthor = ebookAuthor, ebookFilename = "e$id.epub",
            ebookFormat = "epub",
            audiobookId = 1000 + id, audiobookTitle = "A$id", audiobookAuthor = audiobookAuthor,
            audiobookFilename = "a$id.m4b", audiobookFormat = "m4b",
            audiobookDurationSeconds = null,
            status = "transcribing",
            audiobookCoverPath = coverPath,
        )

    private fun queued(pairId: Int, title: String?, status: String = "pending", progress: Float? = null) =
        QueueItemResponse(
            id = 500 + pairId,
            book_pair_id = pairId,
            book_title = title,
            status = status,
            priority = 0,
            progress = progress,
            created_at = "2026-09-14T00:00:00",
        )

    private fun newViewModel(): HomeViewModel {
        val serverUrlManager = mockk<ServerUrlManager>(relaxed = true)
        every { serverUrlManager.currentUrl } returns "https://tandem.example.com"
        every { serverUrlManager.serverUrlFlow } returns emptyFlow()
        val networkMonitor = mockk<NetworkMonitor>(relaxed = true)
        every { networkMonitor.isOnline } returns MutableStateFlow(true)
        val loader = mockk<LibraryLoader>(relaxed = true)
        every { loader.state } returns MutableStateFlow(LibraryLoadState.Loaded)
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
            loader = loader,
        )
    }

    @Test
    fun `a queued pair carries the cached pair's cover and author`() {
        every { transcriptionRepository.activeQueueItemsFlow() } returns
            flowOf(listOf(queued(7, "Queued Title")))
        every { repository.getPairsFlow() } returns flowOf(
            listOf(pair(7, "/api/files/covers/audiobook_1007.jpg", ebookAuthor = null, audiobookAuthor = "Narrated Author"))
        )

        val item = newViewModel().queueItems.value.single()

        assertEquals(7, item.pairId)
        assertEquals(1007, item.audiobookId)
        assertEquals("/api/files/covers/audiobook_1007.jpg", item.audiobookCoverPath)
        // Same author rule as every other pair card: the ebook's, else the audiobook's.
        assertEquals("Narrated Author", item.author)
    }

    @Test
    fun `a queue item whose pair is not cached still appears, without a cover`() {
        every { transcriptionRepository.activeQueueItemsFlow() } returns
            flowOf(listOf(queued(8, "Not Yet Synced")))
        every { repository.getPairsFlow() } returns flowOf(emptyList())

        val item = newViewModel().queueItems.value.single()

        assertEquals(8, item.pairId)
        assertEquals("Not Yet Synced", item.title)
        assertNull(item.audiobookId)
        assertNull(item.audiobookCoverPath)
        assertNull(item.author)
    }

    @Test
    fun `status and progress mapping is unchanged`() {
        every { transcriptionRepository.activeQueueItemsFlow() } returns flowOf(
            listOf(
                queued(1, "Running", status = "in_progress", progress = 0.42f),
                queued(2, null, status = "pending"),
            )
        )
        every { repository.getPairsFlow() } returns flowOf(
            listOf(pair(1, null, ebookAuthor = "Author", audiobookAuthor = null))
        )

        val items = newViewModel().queueItems.value

        assertEquals(listOf(1, 2), items.map { it.pairId })
        assertEquals(HomeQueueItem.QueueStatus.TRANSCRIBING, items[0].status)
        assertEquals(42, items[0].percent)
        assertEquals(HomeQueueItem.QueueStatus.QUEUED, items[1].status)
        assertEquals(0, items[1].percent)
        assertEquals("Untitled", items[1].title)
    }
}
