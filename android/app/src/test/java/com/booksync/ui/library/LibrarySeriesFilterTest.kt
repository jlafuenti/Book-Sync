package com.booksync.ui.library

import android.content.Context
import androidx.work.WorkManager
import com.booksync.data.local.SeriesEntry
import com.booksync.data.local.encodeSeries
import com.booksync.data.local.entity.EBookEntity
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
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.Job
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
 * Opening a series from the details screen (issue #916) arrives through
 * [LibraryViewModel.applyDeepLink] with a series and no sort. It must land in
 * series order like [LibraryViewModel.drillIntoSeries] does, not in whatever
 * sort was last used.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class LibrarySeriesFilterTest {

    private val repository = mockk<BookSyncRepository>(relaxed = true)
    private val transcriptionRepository = mockk<TranscriptionRepository>(relaxed = true)
    private val syncMapRemovalStore = mockk<SyncMapRemovalStore>(relaxed = true)

    private val saga = "Axis Saga"
    private val chrono = "Axis Chronological"

    private fun ebook(id: Int, title: String, vararg series: SeriesEntry) = EBookEntity(
        id = id, title = title, author = "An Author", filename = "$id.epub",
        fileSize = null, format = "epub", seriesJson = encodeSeries(series.toList()),
        uploadedAt = "2026-01-01T00:00:00Z",
    )

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        mockkObject(WorkManager.Companion)
        every { WorkManager.getInstance(any<Context>()) } returns mockk(relaxed = true)
        every { transcriptionRepository.activeQueueItemsFlow() } returns emptyFlow()
        every { repository.getPairsFlow() } returns flowOf(emptyList())
        // Primary-series order (saga #1, #2) is the opposite of the chronology's (#2, #1).
        every { repository.getEbooksFlow() } returns flowOf(
            listOf(
                ebook(1, "Axis First", SeriesEntry(saga, 1.0), SeriesEntry(chrono, 2.0)),
                ebook(2, "Axis Second", SeriesEntry(saga, 2.0), SeriesEntry(chrono, 1.0)),
            ),
        )
        every { repository.getAudiobooksFlow() } returns flowOf(emptyList())
        every { repository.getNewPairsFlow() } returns flowOf(emptyList())
        every { repository.getNewEbooksFlow() } returns flowOf(emptyList())
        every { repository.getNewAudiobooksFlow() } returns flowOf(emptyList())
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

    @Test
    fun `a series from the details screen switches to series order`() = runTest {
        val vm = newViewModel()
        vm.setSort(LibrarySort.TitleAsc)

        vm.applyDeepLink(series = chrono)

        assertEquals(chrono, vm.uiState.value.seriesFilter)
        assertEquals(LibrarySort.SeriesOrder, vm.uiState.value.sort)
    }

    @Test
    fun `the filtered list arrives in the order of the opened series`() = runTest {
        val vm = newViewModel()
        backgroundScope.launch(UnconfinedTestDispatcher(testScheduler)) { vm.items.collect { } }

        vm.applyDeepLink(series = chrono)

        assertEquals(listOf(2, 1), vm.items.value.map { it.ebook!!.id })
    }

    @Test
    fun `an explicit sort in the link still wins`() = runTest {
        val vm = newViewModel()

        vm.applyDeepLink(series = chrono, sort = LibrarySort.TitleAsc)

        assertEquals(LibrarySort.TitleAsc, vm.uiState.value.sort)
    }

    @Test
    fun `a link without a series leaves the sort alone`() = runTest {
        val vm = newViewModel()
        vm.setSort(LibrarySort.TitleAsc)

        vm.applyDeepLink(filter = LibraryFilter.NEW)

        assertEquals(LibrarySort.TitleAsc, vm.uiState.value.sort)
    }
}
