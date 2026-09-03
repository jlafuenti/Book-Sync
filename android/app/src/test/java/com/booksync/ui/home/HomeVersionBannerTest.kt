package com.booksync.ui.home

import android.content.Context
import androidx.work.WorkManager
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.HealthResponse
import com.booksync.data.remote.SUPPORTED_API_VERSION
import com.booksync.data.remote.ServerUrlManager
import com.booksync.data.remote.ServerVersionGate
import com.booksync.data.remote.VersionBanner
import com.booksync.data.repository.BookSyncRepository
import com.booksync.data.repository.TranscriptionRepository
import com.booksync.data.util.NetworkMonitor
import io.mockk.coEvery
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
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test
import java.io.IOException

/**
 * Home is where the version handshake actually happens (issue #174).
 *
 * It is the first screen that has both a server URL and a session, and it is the
 * screen a returning user lands on without passing through login at all — so a
 * check that lived only on the first-run screen would never run again after the
 * install that set the server up.
 *
 * The banner is a ViewModel value rather than something the composable derives,
 * because the composable is not measured by any test in this repo and this is
 * exactly the logic that must not silently stop firing.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class HomeVersionBannerTest {

    private val repository = mockk<BookSyncRepository>(relaxed = true)
    private val transcriptionRepository = mockk<TranscriptionRepository>(relaxed = true)
    private lateinit var api: BookSyncApi

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        mockkObject(WorkManager.Companion)
        every { WorkManager.getInstance(any<Context>()) } returns mockk(relaxed = true)
        every { transcriptionRepository.activeQueueItemsFlow() } returns emptyFlow()
        api = mockk()
    }

    @After
    fun tearDown() {
        unmockkObject(WorkManager.Companion)
        Dispatchers.resetMain()
    }

    private fun gateReporting(apiVersion: Int?): ServerVersionGate {
        coEvery { api.getHealth(any()) } returns HealthResponse(
            status = "healthy",
            api_version = apiVersion,
        )
        val urls = mockk<ServerUrlManager>()
        every { urls.currentUrl } returns "https://tandem.example.com"
        return ServerVersionGate(api, urls)
    }

    private fun newViewModel(gate: ServerVersionGate): HomeViewModel {
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
            serverVersionGate = gate,
            context = mockk(relaxed = true),
        )
    }

    @Test
    fun `a newer server asks the user to update the app`() {
        val vm = newViewModel(gateReporting(SUPPORTED_API_VERSION + 1))

        assertEquals(VersionBanner.SERVER_NEWER, vm.versionBanner.value)
    }

    @Test
    fun `an older server asks for the server to be upgraded`() {
        val vm = newViewModel(gateReporting(SUPPORTED_API_VERSION - 1))

        assertEquals(VersionBanner.SERVER_OLDER, vm.versionBanner.value)
    }

    @Test
    fun `a matching server shows nothing`() {
        val vm = newViewModel(gateReporting(SUPPORTED_API_VERSION))

        assertNull(vm.versionBanner.value)
    }

    @Test
    fun `a server that reports no version shows nothing`() {
        // Every server running today. A banner here would greet every existing
        // user with a warning about a problem they do not have.
        val vm = newViewModel(gateReporting(null))

        assertNull(vm.versionBanner.value)
    }

    @Test
    fun `an unreachable server shows nothing rather than a scary banner`() {
        coEvery { api.getHealth(any()) } throws IOException("Unable to resolve host")
        val urls = mockk<ServerUrlManager>()
        every { urls.currentUrl } returns "https://tandem.example.com"

        val vm = newViewModel(ServerVersionGate(api, urls))

        assertNull(vm.versionBanner.value)
    }

    @Test
    fun `opening Home is what triggers the check`() {
        val gate = gateReporting(SUPPORTED_API_VERSION)

        newViewModel(gate)

        coVerify(exactly = 1) { api.getHealth("https://tandem.example.com/api/health") }
    }
}
