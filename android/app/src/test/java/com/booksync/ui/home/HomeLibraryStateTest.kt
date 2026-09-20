package com.booksync.ui.home

import android.content.Context
import androidx.work.WorkManager
import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.local.entity.EBookEntity
import com.booksync.data.remote.ServerUrlManager
import com.booksync.data.remote.ServerVersionGate
import com.booksync.data.repository.BookSyncRepository
import com.booksync.data.repository.LibraryLoadState
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
import kotlinx.coroutines.flow.MutableSharedFlow
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
 * Issue #624: Home's "Your library is empty" copy showed whenever its own four
 * carousels (Continue Reading / Recently Added / New Pairs / In Queue) were empty —
 * even for an account whose library is full but has not started anything, which is
 * exactly the demo account a Play reviewer lands on after "Try the demo" (#147).
 *
 * [HomeViewModel.libraryState] answers the question those four carousels can't:
 * does the account have *any* books at all (pairs, standalone ebooks, or standalone
 * audiobooks), independent of whether anything has been opened. It is built from
 * [BookSyncRepository.getPairsFlow], [BookSyncRepository.getEbooksFlow] and
 * [BookSyncRepository.getAudiobooksFlow] — the same three flows LibraryViewModel
 * already combines to build the Library tab's own list — so no new query exists
 * anywhere below it.
 *
 * LOADING is deliberately not "the app is talking to the network": it is simply
 * "these three flows have not all produced a value yet". That is what lets a fresh
 * subscriber tell "nothing has synced from Room yet" apart from "synced, and there
 * truly are zero books" — the distinction the empty-state screen needs before it can
 * safely choose between its two messages.
 *
 * Issue #641 folds a fourth source in: [LibraryLoader.state]. Before this, nothing
 * called `refreshPairs/refreshEbooks/refreshAudiobooks` until the Library tab's own
 * `init` ran, so a fresh sign-in that landed on Home first saw LOADING resolve to
 * EMPTY the instant Room's three (genuinely empty) flows reported in — no fetch was
 * ever in flight to wait for. Now LOADING also holds while [LibraryLoader] itself is
 * still `Idle`/`Loading`, and only resolves to EMPTY once it has settled
 * (`PairsLoaded`/`Loaded`/`Failed`) with nothing to show. Any cached rows still win
 * outright — HAS_BOOKS regardless of what the loader is doing — so a slow or failed
 * refresh never hides a library Room already has cached.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class HomeLibraryStateTest {

    private val repository = mockk<BookSyncRepository>(relaxed = true)
    private val transcriptionRepository = mockk<TranscriptionRepository>(relaxed = true)

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
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
        audiobookId = 1000 + id, audiobookTitle = "A$id", audiobookAuthor = null,
        audiobookFilename = "a$id.m4b", audiobookFormat = "m4b",
        audiobookDurationSeconds = null,
        status = "matched",
    )

    private fun ebook(id: Int) = EBookEntity(
        id = id,
        title = "Ebook $id",
        author = null,
        filename = "e$id.epub",
        fileSize = null,
        format = "epub",
        series = null,
        seriesIndex = null,
        uploadedAt = "2026-09-14T00:00:00",
    )

    private fun audiobook(id: Int) = AudioBookEntity(
        id = id,
        title = "Audiobook $id",
        author = null,
        filename = "a$id.m4b",
        durationSeconds = null,
        format = "m4b",
        series = null,
        seriesIndex = null,
        uploadedAt = "2026-09-14T00:00:00",
    )

    /**
     * Defaults to `Loaded` — a settled loader with nothing further coming — so
     * tests that only care about the three Room flows don't also have to reason
     * about the loader. Tests that exercise the loader interaction pass their
     * own.
     */
    private fun newViewModel(
        loaderState: LibraryLoadState = LibraryLoadState.Loaded,
    ): HomeViewModel {
        val serverUrlManager = mockk<ServerUrlManager>(relaxed = true)
        every { serverUrlManager.currentUrl } returns "https://tandem.example.com"
        every { serverUrlManager.serverUrlFlow } returns emptyFlow()
        val networkMonitor = mockk<NetworkMonitor>(relaxed = true)
        every { networkMonitor.isOnline } returns MutableStateFlow(true)
        val loader = mockk<LibraryLoader>(relaxed = true)
        every { loader.state } returns MutableStateFlow(loaderState)
        return HomeViewModel(
            repository = repository,
            transcriptionRepository = transcriptionRepository,
            networkMonitor = networkMonitor,
            serverUrlManager = serverUrlManager,
            serverVersionGate = ServerVersionGate(mockk(relaxed = true), serverUrlManager),
            context = mockk(relaxed = true),
            loader = loader,
        )
    }

    @Test
    fun `a library with no pairs, ebooks or audiobooks is EMPTY`() {
        every { repository.getPairsFlow() } returns flowOf(emptyList())
        every { repository.getEbooksFlow() } returns flowOf(emptyList())
        every { repository.getAudiobooksFlow() } returns flowOf(emptyList())

        val vm = newViewModel()

        assertEquals(HomeLibraryState.EMPTY, vm.libraryState.value)
    }

    @Test
    fun `standalone books with no pairs still count as HAS_BOOKS`() {
        // The demo-account shape this issue is about: books the reviewer never
        // paired or opened, so none of Home's four carousels have anything either.
        every { repository.getPairsFlow() } returns flowOf(emptyList())
        every { repository.getEbooksFlow() } returns flowOf(listOf(ebook(1)))
        every { repository.getAudiobooksFlow() } returns flowOf(listOf(audiobook(2)))

        val vm = newViewModel()

        assertEquals(HomeLibraryState.HAS_BOOKS, vm.libraryState.value)
    }

    @Test
    fun `pairs alone also count as HAS_BOOKS`() {
        every { repository.getPairsFlow() } returns flowOf(listOf(pair(1)))
        every { repository.getEbooksFlow() } returns flowOf(emptyList())
        every { repository.getAudiobooksFlow() } returns flowOf(emptyList())

        val vm = newViewModel()

        assertEquals(HomeLibraryState.HAS_BOOKS, vm.libraryState.value)
    }

    @Test
    fun `library state starts LOADING before the three flows have all produced a value`() {
        // No-replay shared flows: nothing has been emitted yet, so this models
        // "subscribed to Room but no query has completed" rather than "queried
        // and got zero rows".
        every { repository.getPairsFlow() } returns MutableSharedFlow(replay = 0, extraBufferCapacity = 1)
        every { repository.getEbooksFlow() } returns MutableSharedFlow(replay = 0, extraBufferCapacity = 1)
        every { repository.getAudiobooksFlow() } returns MutableSharedFlow(replay = 0, extraBufferCapacity = 1)

        val vm = newViewModel()

        assertEquals(HomeLibraryState.LOADING, vm.libraryState.value)
    }

    @Test
    fun `library state transitions from LOADING to a real answer once all three flows report in`() {
        // extraBufferCapacity so tryEmit always succeeds regardless of exactly
        // when combine's collector for that particular upstream subscribes.
        val pairs = MutableSharedFlow<List<BookPairEntity>>(replay = 0, extraBufferCapacity = 1)
        val ebooks = MutableSharedFlow<List<EBookEntity>>(replay = 0, extraBufferCapacity = 1)
        val audiobooks = MutableSharedFlow<List<AudioBookEntity>>(replay = 0, extraBufferCapacity = 1)
        every { repository.getPairsFlow() } returns pairs
        every { repository.getEbooksFlow() } returns ebooks
        every { repository.getAudiobooksFlow() } returns audiobooks

        val vm = newViewModel()
        assertEquals(HomeLibraryState.LOADING, vm.libraryState.value)

        assertEquals(true, pairs.tryEmit(emptyList()))
        assertEquals(HomeLibraryState.LOADING, vm.libraryState.value)
        assertEquals(true, ebooks.tryEmit(emptyList()))
        assertEquals(HomeLibraryState.LOADING, vm.libraryState.value)
        assertEquals(true, audiobooks.tryEmit(listOf(audiobook(1))))

        assertEquals(HomeLibraryState.HAS_BOOKS, vm.libraryState.value)
    }

    // --- Issue #641: LibraryLoader folded into libraryState --------------------

    @Test
    fun `empty Room stays LOADING while the loader is Idle`() {
        every { repository.getPairsFlow() } returns flowOf(emptyList())
        every { repository.getEbooksFlow() } returns flowOf(emptyList())
        every { repository.getAudiobooksFlow() } returns flowOf(emptyList())

        val vm = newViewModel(loaderState = LibraryLoadState.Idle)

        assertEquals(HomeLibraryState.LOADING, vm.libraryState.value)
    }

    @Test
    fun `empty Room stays LOADING while the loader is Loading`() {
        every { repository.getPairsFlow() } returns flowOf(emptyList())
        every { repository.getEbooksFlow() } returns flowOf(emptyList())
        every { repository.getAudiobooksFlow() } returns flowOf(emptyList())

        val vm = newViewModel(loaderState = LibraryLoadState.Loading)

        assertEquals(HomeLibraryState.LOADING, vm.libraryState.value)
    }

    @Test
    fun `empty Room becomes EMPTY once the loader has settled as Loaded`() {
        every { repository.getPairsFlow() } returns flowOf(emptyList())
        every { repository.getEbooksFlow() } returns flowOf(emptyList())
        every { repository.getAudiobooksFlow() } returns flowOf(emptyList())

        val vm = newViewModel(loaderState = LibraryLoadState.Loaded)

        assertEquals(HomeLibraryState.EMPTY, vm.libraryState.value)
    }

    @Test
    fun `empty Room becomes EMPTY once the loader has settled as Failed`() {
        // A refresh that never got past pairs (offline on first launch, say)
        // must still resolve to a real answer rather than spinning forever.
        every { repository.getPairsFlow() } returns flowOf(emptyList())
        every { repository.getEbooksFlow() } returns flowOf(emptyList())
        every { repository.getAudiobooksFlow() } returns flowOf(emptyList())

        val vm = newViewModel(loaderState = LibraryLoadState.Failed)

        assertEquals(HomeLibraryState.EMPTY, vm.libraryState.value)
    }

    @Test
    fun `rows in Room win outright as HAS_BOOKS even while the loader is still Loading`() {
        every { repository.getPairsFlow() } returns flowOf(listOf(pair(1)))
        every { repository.getEbooksFlow() } returns flowOf(emptyList())
        every { repository.getAudiobooksFlow() } returns flowOf(emptyList())

        val vm = newViewModel(loaderState = LibraryLoadState.Loading)

        assertEquals(HomeLibraryState.HAS_BOOKS, vm.libraryState.value)
    }

    /**
     * Constructs a `HomeViewModel` with the loader parked at [initialState] and
     * asserts how many times `init` called `loader.refresh()`.
     */
    private fun assertInitRefreshCallCount(initialState: LibraryLoadState, expectedCalls: Int) {
        every { repository.getPairsFlow() } returns flowOf(emptyList())
        every { repository.getEbooksFlow() } returns flowOf(emptyList())
        every { repository.getAudiobooksFlow() } returns flowOf(emptyList())
        val serverUrlManager = mockk<ServerUrlManager>(relaxed = true)
        every { serverUrlManager.currentUrl } returns "https://tandem.example.com"
        every { serverUrlManager.serverUrlFlow } returns emptyFlow()
        val networkMonitor = mockk<NetworkMonitor>(relaxed = true)
        every { networkMonitor.isOnline } returns MutableStateFlow(true)
        val loader = mockk<LibraryLoader>(relaxed = true)
        every { loader.state } returns MutableStateFlow(initialState)

        HomeViewModel(
            repository = repository,
            transcriptionRepository = transcriptionRepository,
            networkMonitor = networkMonitor,
            serverUrlManager = serverUrlManager,
            serverVersionGate = ServerVersionGate(mockk(relaxed = true), serverUrlManager),
            context = mockk(relaxed = true),
            loader = loader,
        )

        verify(exactly = expectedCalls) { loader.refresh() }
    }

    @Test
    fun `init starts the loader when it is Idle`() {
        assertInitRefreshCallCount(LibraryLoadState.Idle, expectedCalls = 1)
    }

    /**
     * A sign-in while offline leaves the loader `Failed` (pairs never loaded
     * this sign-in). Without this, every later Home — a tab switch back, a
     * relaunch within the same process — would sit on EMPTY forever, since
     * only `Idle` used to retry and nothing else ever moves the loader off
     * `Failed` on its own; only opening Library (whose own `refresh()` isn't
     * gated by loader state) recovered it.
     */
    @Test
    fun `init retries the loader when it is Failed`() {
        assertInitRefreshCallCount(LibraryLoadState.Failed, expectedCalls = 1)
    }

    @Test
    fun `init does not start the loader when it is Loading`() {
        assertInitRefreshCallCount(LibraryLoadState.Loading, expectedCalls = 0)
    }

    @Test
    fun `init does not start the loader when it is PairsLoaded`() {
        assertInitRefreshCallCount(LibraryLoadState.PairsLoaded, expectedCalls = 0)
    }

    @Test
    fun `init does not start the loader when it is Loaded`() {
        assertInitRefreshCallCount(LibraryLoadState.Loaded, expectedCalls = 0)
    }
}
