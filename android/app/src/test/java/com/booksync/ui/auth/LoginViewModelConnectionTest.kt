package com.booksync.ui.auth

import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.HealthResponse
import com.booksync.data.remote.INVALID_SERVER_URL_MESSAGE
import com.booksync.data.remote.ServerUrlManager
import com.booksync.data.remote.TokenManager
import com.booksync.data.remote.UserScopeProvider
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import retrofit2.HttpException
import retrofit2.Response
import java.io.IOException

/**
 * "Check connection" on the first-run screen (issue #175).
 *
 * Someone installing from Play has no way to tell a typo from a server that is
 * down, and the previous screen answered neither: it took whatever was typed and
 * only failed later, at sign-in. The probe exists to answer "is there a Tandem
 * server at this address" before credentials are involved.
 *
 * The one hard rule is that it never throws. It runs on a string a stranger just
 * typed, so unreachable hosts, wrong ports, HTML from a reverse proxy and
 * unparseable bodies are all ordinary inputs, and every one of them has to end
 * as a sentence on screen.
 */
private const val PROBE_SERVER_URL = "https://tandem.example.com"

@OptIn(ExperimentalCoroutinesApi::class)
class LoginViewModelConnectionTest {

    private lateinit var api: BookSyncApi
    private lateinit var serverUrlManager: ServerUrlManager

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())

        api = mockk()
        serverUrlManager = mockk()
        every { serverUrlManager.serverUrlFlow } returns flowOf("")
        every { serverUrlManager.currentUrl } returns ""
        coEvery { serverUrlManager.setServerUrl(any()) } returns true
        coEvery { api.getHealth() } returns HealthResponse(status = "healthy")
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    private fun newViewModel() = LoginViewModel(
        api = api,
        tokenManager = mockk<TokenManager>(relaxed = true),
        serverUrlManager = serverUrlManager,
        userScopeProvider = mockk<UserScopeProvider>(relaxed = true),
    )

    @Test
    fun `a healthy server maps to Connected`() {
        val vm = newViewModel()

        vm.checkConnection(PROBE_SERVER_URL)

        assertEquals(ConnectionState.Connected, vm.connectionState.value)
    }

    @Test
    fun `the probed url is stored so the next request goes to it`() {
        // BaseUrlInterceptor reads the stored URL per request, so storing it is
        // what makes the probe hit the host the user typed rather than the
        // placeholder Retrofit was built with.
        val vm = newViewModel()

        vm.checkConnection("tandem.example.com")

        coVerify(exactly = 1) { serverUrlManager.setServerUrl("https://tandem.example.com") }
    }

    @Test
    fun `an unreachable server maps to a readable failure, not an exception`() {
        coEvery { api.getHealth() } throws IOException("failed to connect")

        val vm = newViewModel()
        vm.checkConnection(PROBE_SERVER_URL)

        val state = vm.connectionState.value
        assertTrue(state is ConnectionState.Failed)
        assertTrue((state as ConnectionState.Failed).message.isNotBlank())
    }

    @Test
    fun `an http error maps to a readable failure naming the status`() {
        coEvery { api.getHealth() } throws HttpException(
            Response.error<Unit>(503, """{"status":"unhealthy"}""".toResponseBody("application/json".toMediaType())),
        )

        val vm = newViewModel()
        vm.checkConnection(PROBE_SERVER_URL)

        val state = vm.connectionState.value
        assertTrue(state is ConnectionState.Failed)
        assertTrue((state as ConnectionState.Failed).message.contains("503"))
    }

    @Test
    fun `a 404 is reported as not a Tandem server rather than as a network error`() {
        // Someone pointing the app at their router's admin page gets a 200 or a
        // 404 from something that is not Tandem; "couldn't reach it" would be a
        // lie that sends them to check their wifi.
        coEvery { api.getHealth() } throws HttpException(
            Response.error<Unit>(404, "".toResponseBody(null)),
        )

        val vm = newViewModel()
        vm.checkConnection(PROBE_SERVER_URL)

        assertTrue(vm.connectionState.value is ConnectionState.Failed)
    }

    @Test
    fun `an unparseable body is a failure, not a crash`() {
        // A reverse proxy or captive portal answers 200 with HTML; kotlinx
        // serialization throws on it inside the Retrofit converter.
        coEvery { api.getHealth() } throws RuntimeException("Unexpected JSON token")

        val vm = newViewModel()
        vm.checkConnection(PROBE_SERVER_URL)

        assertTrue(vm.connectionState.value is ConnectionState.Failed)
    }

    @Test
    fun `an address that is not a usable url is refused before any request`() {
        val vm = newViewModel()

        vm.checkConnection("https:/typo")

        val state = vm.connectionState.value
        assertTrue(state is ConnectionState.Failed)
        assertEquals(INVALID_SERVER_URL_MESSAGE, (state as ConnectionState.Failed).message)
        coVerify(exactly = 0) { api.getHealth() }
        coVerify(exactly = 0) { serverUrlManager.setServerUrl(any()) }
    }

    @Test
    fun `a url the manager refuses is reported rather than silently ignored`() {
        coEvery { serverUrlManager.setServerUrl(any()) } returns false

        val vm = newViewModel()
        vm.checkConnection(PROBE_SERVER_URL)

        assertTrue(vm.connectionState.value is ConnectionState.Failed)
        coVerify(exactly = 0) { api.getHealth() }
    }

    @Test
    fun `a server too old to report a status still counts as reachable`() {
        // The DTO's fields are nullable on purpose: a server predating the
        // version fields must not be reported as broken.
        coEvery { api.getHealth() } returns HealthResponse()

        val vm = newViewModel()
        vm.checkConnection(PROBE_SERVER_URL)

        assertEquals(ConnectionState.Connected, vm.connectionState.value)
    }

    @Test
    fun `re-checking clears the previous result`() {
        coEvery { api.getHealth() } throws IOException("offline")
        val vm = newViewModel()
        vm.checkConnection(PROBE_SERVER_URL)
        assertTrue(vm.connectionState.value is ConnectionState.Failed)

        coEvery { api.getHealth() } returns HealthResponse(status = "healthy")
        vm.checkConnection(PROBE_SERVER_URL)

        assertEquals(ConnectionState.Connected, vm.connectionState.value)
    }
}
