package com.booksync.ui.home

import android.content.Context
import androidx.work.WorkManager
import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.local.entity.EBookEntity
import com.booksync.data.local.entity.UserProgressEntity
import com.booksync.data.remote.ServerUrlManager
import com.booksync.data.repository.BookSyncRepository
import com.booksync.data.repository.LibraryLoadState
import com.booksync.data.repository.LibraryLoader
import com.booksync.data.repository.TEST_SCOPE
import com.booksync.data.repository.TranscriptionRepository
import com.booksync.data.util.NetworkMonitor
import io.mockk.every
import io.mockk.mockk
import io.mockk.mockkObject
import io.mockk.unmockkObject
import java.time.Instant
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test

/**
 * Issue #716: the home screen's Next up row, fed from the whole Room library
 * (not the recently-played flows, which drop finished books, the case that
 * matters most here). The rule itself is pinned by [NextUpParityTest]; this
 * checks the ViewModel feeds it and hands the screen ids it can open.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class HomeNextUpTest {

    private val repository = mockk<BookSyncRepository>(relaxed = true)
    private val transcriptionRepository = mockk<TranscriptionRepository>(relaxed = true)
    private val twoDaysAgo = Instant.now().minusSeconds(2 * 86_400).toString()

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
    }

    @After
    fun tearDown() {
        unmockkObject(WorkManager.Companion)
        Dispatchers.resetMain()
    }

    private fun ebook(id: Int, index: Float) = EBookEntity(
        id = id, title = "Axis Book $id", author = "An Author", filename = "e$id.epub", fileSize = 1L,
        format = "epub", series = "Axis", seriesIndex = index,
        uploadedAt = "2026-01-01T00:00:00Z", isDownloaded = false,
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
            serverVersionGate = com.booksync.data.remote.ServerVersionGate(mockk(relaxed = true), serverUrlManager),
            context = mockk(relaxed = true),
            loader = loader,
        )
    }

    @Test
    fun `finishing a series book puts the next one in Next up, ready to open`() = runTest(UnconfinedTestDispatcher()) {
        every { repository.getPairsFlow() } returns flowOf(emptyList<BookPairEntity>())
        every { repository.getAudiobooksFlow() } returns flowOf(emptyList<AudioBookEntity>())
        every { repository.getEbooksFlow() } returns flowOf(listOf(ebook(1, 1f), ebook(2, 2f)))
        every { repository.getAllProgressFlow() } returns flowOf(
            listOf(
                UserProgressEntity(
                    scopeKey = TEST_SCOPE, mediaType = "ebook", mediaId = 1, bookPairId = null,
                    epubCfi = null, epubChapter = null, epubProgressPercent = 100f, audioPositionMs = null,
                    isCompleted = true, updatedAt = 1_000L, deviceId = null, capturedAt = twoDaysAgo,
                ),
            ),
        )
        val vm = newViewModel()
        backgroundScope.launch { vm.nextUpItems.collect {} }

        val item = vm.nextUpItems.value.single()
        assertEquals("ebook_2", item.id)
        assertEquals(HomeItem.MediaType.EBOOK, item.mediaType)
        assertEquals(2, item.ebookId)
        assertEquals("Axis", item.series)
        assertEquals(2f, item.seriesIndex)
    }

    /**
     * The tour scrolls by position in HomeScreen's `sections` list, which must
     * mirror the LazyColumn's rows. A Next up row missing from it would send the
     * tour one row short of Recently Added. Source guard, as the composable is
     * excluded from Kover.
     */
    @Test
    fun `the tour's section list keeps Next up in the same place as the rows`() {
        var dir = java.io.File("").absoluteFile
        var screen: String? = null
        repeat(4) {
            listOf("app/src/main/java", "src/main/java").forEach { root ->
                val f = java.io.File(dir, "$root/com/booksync/ui/home/HomeScreen.kt")
                if (screen == null && f.exists()) screen = f.readText()
            }
            dir = dir.parentFile ?: return@repeat
        }
        val source = screen ?: error("HomeScreen.kt not found")

        val sections = source.substringAfter("val sections = buildList").substringBefore("LaunchedEffect")
        assertOrdered(sections, "continueItems", "nextUpItems", "recentlyAdded")

        val rows = source.substringAfter("LazyColumn(")
        assertOrdered(rows, "title = \"Continue Reading\"", "title = \"Next up\"", "title = \"Recently Added\"")
    }

    private fun assertOrdered(text: String, vararg markers: String) {
        val at = markers.map { m -> text.indexOf(m).also { require(it >= 0) { "missing: $m" } } }
        assertEquals("order of ${markers.toList()}", at.sorted(), at)
    }

    @Test
    fun `nothing touched means no Next up row`() = runTest(UnconfinedTestDispatcher()) {
        every { repository.getPairsFlow() } returns flowOf(emptyList<BookPairEntity>())
        every { repository.getAudiobooksFlow() } returns flowOf(emptyList<AudioBookEntity>())
        every { repository.getEbooksFlow() } returns flowOf(listOf(ebook(1, 1f), ebook(2, 2f)))
        every { repository.getAllProgressFlow() } returns flowOf(emptyList())
        val vm = newViewModel()
        backgroundScope.launch { vm.nextUpItems.collect {} }

        assertEquals(emptyList<HomeItem>(), vm.nextUpItems.value)
    }
}
