package com.booksync.ui.home

import android.content.Context
import androidx.work.WorkManager
import com.booksync.data.local.SeriesEntry
import com.booksync.data.local.encodeSeries
import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.local.entity.EBookEntity
import com.booksync.data.local.entity.NextUpDismissalEntity
import com.booksync.data.local.entity.UserProgressEntity
import com.booksync.data.remote.ServerUrlManager
import com.booksync.data.repository.BookSyncRepository
import com.booksync.data.repository.LibraryLoadState
import com.booksync.data.repository.LibraryLoader
import com.booksync.data.repository.TEST_SCOPE
import com.booksync.data.repository.TranscriptionRepository
import com.booksync.data.util.NetworkMonitor
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import io.mockk.mockkObject
import io.mockk.unmockkObject
import java.time.Instant
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Issue #923: removing a series from Home's Next up row. The rule (a dismissal
 * at or after the series' last activity hides it) is [computeNextUp]'s and is
 * pinned by [NextUpParityTest]; this checks the ViewModel feeds the stored
 * dismissals in, tells the repository, and announces the removal for the
 * snackbar.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class HomeRemoveFromNextUpTest {

    private val repository = mockk<BookSyncRepository>(relaxed = true)
    private val transcriptionRepository = mockk<TranscriptionRepository>(relaxed = true)
    private val twoDaysAgo = Instant.now().minusSeconds(2 * 86_400)
    private val dismissals = MutableStateFlow<List<NextUpDismissalEntity>>(emptyList())

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        mockkObject(WorkManager.Companion)
        every { WorkManager.getInstance(any<Context>()) } returns mockk(relaxed = true)
        every { transcriptionRepository.activeQueueItemsFlow() } returns emptyFlow()
        every { repository.getRecentlyPlayedPairsFlow() } returns flowOf(emptyList())
        every { repository.getRecentlyPlayedStandaloneAudiobooksFlow() } returns flowOf(emptyList())
        every { repository.getRecentlyReadEbooksFlow() } returns flowOf(emptyList())
        every { repository.getAllBookmarksFlow() } returns flowOf(emptyList())
        every { repository.getPairsFlow() } returns flowOf(emptyList<BookPairEntity>())
        every { repository.getAudiobooksFlow() } returns flowOf(emptyList<AudioBookEntity>())
        every { repository.getEbooksFlow() } returns flowOf(listOf(ebook(1, 1.0), ebook(2, 2.0)))
        every { repository.getAllProgressFlow() } returns flowOf(listOf(finished(1)))
        every { repository.getNextUpDismissalsFlow() } returns dismissals
    }

    @After
    fun tearDown() {
        unmockkObject(WorkManager.Companion)
        Dispatchers.resetMain()
    }

    private fun ebook(id: Int, index: Double) = EBookEntity(
        id = id, title = "Axis Book $id", author = "An Author", filename = "e$id.epub", fileSize = 1L,
        format = "epub", seriesJson = encodeSeries(listOf(SeriesEntry("Axis Saga", index))),
        uploadedAt = "2026-01-01T00:00:00Z", isDownloaded = false,
    )

    private fun finished(ebookId: Int) = UserProgressEntity(
        scopeKey = TEST_SCOPE, mediaType = "ebook", mediaId = ebookId, bookPairId = null,
        epubCfi = null, epubChapter = null, epubProgressPercent = 100f, audioPositionMs = null,
        isCompleted = true, updatedAt = 1_000L, deviceId = null, capturedAt = twoDaysAgo.toString(),
    )

    private fun dismissal(atMs: Long) = NextUpDismissalEntity("axis saga", "Axis Saga", atMs)

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
            serverVersionGate = com.booksync.data.remote.ServerVersionGate(mockk(relaxed = true), serverUrlManager),
            context = mockk(relaxed = true),
            loader = loader,
        )
    }

    @Test
    fun `a card in Next up carries the series it is there for`() = runTest(UnconfinedTestDispatcher()) {
        val vm = newViewModel()
        backgroundScope.launch { vm.nextUpItems.collect {} }

        assertEquals("Axis Saga", vm.nextUpItems.value.single().nextUpSeries)
    }

    @Test
    fun `a dismissal after the last activity hides the row and removing it brings it back`() =
        runTest(UnconfinedTestDispatcher()) {
            val vm = newViewModel()
            backgroundScope.launch { vm.nextUpItems.collect {} }
            assertEquals(1, vm.nextUpItems.value.size)

            dismissals.value = listOf(dismissal(Instant.now().toEpochMilli()))
            assertTrue(vm.nextUpItems.value.isEmpty())

            dismissals.value = emptyList()
            assertEquals("ebook_2", vm.nextUpItems.value.single().id)
        }

    @Test
    fun `a dismissal older than the last activity does not hide the row`() = runTest(UnconfinedTestDispatcher()) {
        dismissals.value = listOf(dismissal(twoDaysAgo.toEpochMilli() - 60_000L))
        val vm = newViewModel()
        backgroundScope.launch { vm.nextUpItems.collect {} }

        assertEquals(1, vm.nextUpItems.value.size)
    }

    @Test
    fun `removeFromNextUp tells the repository and announces it`() = runTest(UnconfinedTestDispatcher()) {
        val vm = newViewModel()

        vm.removeFromNextUp("Axis Saga")

        coVerify { repository.dismissNextUp("Axis Saga") }
        assertEquals(NextUpEvent.Removed("Axis Saga"), vm.nextUpEvents.first())
    }

    @Test
    fun `undoRemoveFromNextUp tells the repository`() = runTest(UnconfinedTestDispatcher()) {
        val vm = newViewModel()

        vm.undoRemoveFromNextUp("Axis Saga")

        coVerify { repository.undoDismissNextUp("Axis Saga") }
    }
}
