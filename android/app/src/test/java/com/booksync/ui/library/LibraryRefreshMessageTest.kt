package com.booksync.ui.library

import android.content.Context
import androidx.work.WorkManager
import com.booksync.data.remote.ServerUrlManager
import com.booksync.data.remote.TokenManager
import com.booksync.data.repository.BookSyncRepository
import com.booksync.data.repository.LastOpenedTimes
import com.booksync.data.repository.LibraryLoader
import com.booksync.data.repository.TranscriptionRepository
import com.booksync.data.util.NetworkMonitor
import io.mockk.every
import io.mockk.mockk
import io.mockk.mockkObject
import io.mockk.unmockkObject
import java.net.ConnectException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import retrofit2.HttpException
import retrofit2.Response

/**
 * Issue #641: `LibraryViewModel.refresh` used to run
 * `repository.refreshPairs/refreshEbooks/refreshAudiobooks` itself, inside a
 * try/catch that turned each network failure into a snackbar message. That fetch
 * now lives in [LibraryLoader] instead — shared with [com.booksync.ui.home.HomeViewModel]
 * so a fresh sign-in's library loads before the Library tab is ever opened — and
 * `refresh` delegates to it (`loader.refresh().join()`), then reads
 * [LibraryLoader.lastError] to reproduce the exact same messages as before. This
 * pins that the message mapping survived the move.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class LibraryRefreshMessageTest {

    private val repository = mockk<BookSyncRepository>(relaxed = true)
    private val transcriptionRepository = mockk<TranscriptionRepository>(relaxed = true)

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        // WorkManager.getInstance is a Kotlin companion function, so the
        // companion object (not a Java static) is what needs mocking — same
        // as SearchDownloadTest.
        mockkObject(WorkManager.Companion)
        every { WorkManager.getInstance(any<Context>()) } returns mockk(relaxed = true)
        every { transcriptionRepository.activeQueueItemsFlow() } returns emptyFlow()
        every { repository.getPairsFlow() } returns flowOf(emptyList())
        // refresh() also sweeps recently-opened pairs for a streamed-book
        // sync-map prefetch (issue #655 follow-up #2 — lastOpenedTimesFlow,
        // not getRecentlyPlayedPairsFlow: see LibrarySyncMapPrefetchTest for
        // why); an unstubbed relaxed mock's Flow completes without emitting,
        // and .first() on that throws NoSuchElementException, not an empty
        // list — same reason getPairsFlow() above needs its own explicit stub.
        every { repository.lastOpenedTimesFlow() } returns flowOf(LastOpenedTimes())
    }

    @After
    fun tearDown() {
        unmockkObject(WorkManager.Companion)
        Dispatchers.resetMain()
    }

    /** A [Job] that reports itself already complete, so `loader.refresh().join()` returns at once. */
    private fun completedJob(): Job = Job().apply { complete() }

    private fun newViewModel(loader: LibraryLoader): LibraryViewModel {
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
        )
    }

    private fun newLoader(error: Throwable?): LibraryLoader {
        val loader = mockk<LibraryLoader>(relaxed = true)
        every { loader.refresh() } returns completedJob()
        every { loader.lastError } returns MutableStateFlow(error)
        return loader
    }

    @Test
    fun `a loader failure with UnknownHostException shows the offline message`() = runTest {
        val vm = newViewModel(newLoader(UnknownHostException("no route to host")))

        vm.refresh(silent = false)

        assertEquals("Offline — showing cached library", vm.refreshMessage.value)
    }

    @Test
    fun `a loader failure with ConnectException shows the server-unreachable message`() = runTest {
        val vm = newViewModel(newLoader(ConnectException("refused")))

        vm.refresh(silent = false)

        assertEquals("Server unreachable", vm.refreshMessage.value)
    }

    @Test
    fun `a loader failure with SocketTimeoutException shows the timeout message`() = runTest {
        val vm = newViewModel(newLoader(SocketTimeoutException("timed out")))

        vm.refresh(silent = false)

        assertEquals("Server unreachable (timeout)", vm.refreshMessage.value)
    }

    @Test
    fun `a loader failure with HttpException names the status code`() = runTest {
        val error = HttpException(Response.error<Unit>(503, "".toResponseBody(null)))
        val vm = newViewModel(newLoader(error))

        vm.refresh(silent = false)

        assertEquals("Server error: HTTP 503", vm.refreshMessage.value)
    }

    @Test
    fun `a loader failure with an unrecognized exception falls back to its message`() = runTest {
        val vm = newViewModel(newLoader(IllegalStateException("weird")))

        vm.refresh(silent = false)

        assertEquals("Refresh failed: weird", vm.refreshMessage.value)
    }

    @Test
    fun `a successful non-silent refresh reports the refreshed message`() = runTest {
        val vm = newViewModel(newLoader(error = null))

        vm.refresh(silent = false)

        assertEquals("Library refreshed", vm.refreshMessage.value)
    }

    @Test
    fun `a successful silent refresh reports no message`() = runTest {
        val vm = newViewModel(newLoader(error = null))
        vm.clearRefreshMessage()

        vm.refresh(silent = true)

        assertEquals(null, vm.refreshMessage.value)
    }

    /**
     * Before the loader existed, everything after the three fetches —
     * `getPairsFlow().first()`, `fetchMissingSyncMaps` — sat inside the same
     * try/catch as the fetches themselves, so a failure there became "Refresh
     * failed: …" like any other. Once `refresh` started reading
     * `loader.lastError` instead of catching around its own fetch calls, that
     * safety net was lost: an exception here would escape into `viewModelScope`
     * uncaught (a crash) and leave `refreshing` stuck `true`. This pins that a
     * post-join failure is caught and reported exactly like a loader failure,
     * and that `refreshing` still ends `false`.
     */
    @Test
    fun `a failure reading Room after a successful loader run is still caught and reported`() = runTest {
        // Throws on *collection*, not on the call itself — LibraryViewModel's own
        // property initializers (`pairsFlow = repository.getPairsFlow()`) call
        // this synchronously at construction time, outside any try/catch, so a
        // stub that throws from the call itself would fail construction instead
        // of exercising refresh()'s handling of a failure from `.first()`.
        every { repository.getPairsFlow() } returns flow { throw RuntimeException("boom") }
        val vm = newViewModel(newLoader(error = null))

        vm.refresh(silent = false)

        assertEquals("Refresh failed: boom", vm.refreshMessage.value)
        assertEquals(false, vm.refreshing.value)
    }
}
