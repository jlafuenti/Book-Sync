package com.booksync.ui.auth

import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.FirstRunGate
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
import org.junit.Assert.assertFalse
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
 * Two rules, the second learned on a device:
 *
 *  - **It never throws.** It runs on a string a stranger just typed, so
 *    unreachable hosts, wrong ports, HTML from a reverse proxy and unparseable
 *    bodies are all ordinary inputs, and every one of them has to end as a
 *    sentence on screen.
 *  - **It never stores an address it has not verified.** The first cut stored
 *    the URL up front so the base-URL interceptor would route the probe there.
 *    That tore the first-run screen down mid-probe — the sign-in form appeared
 *    over a server that does not exist, the exact outcome #175 is about — and
 *    left the mistyped host configured for every later request.
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
        coEvery { api.getHealth(any()) } returns HealthResponse(status = "healthy")
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    private fun newViewModel(gate: FirstRunGate = FirstRunGate()) = LoginViewModel(
        api = api,
        tokenManager = mockk<TokenManager>(relaxed = true),
        serverUrlManager = serverUrlManager,
        userScopeProvider = mockk<UserScopeProvider>(relaxed = true),
        firstRunGate = gate,
    )

    // -- The probe itself --------------------------------------------------

    @Test
    fun `a healthy server maps to Connected`() {
        val vm = newViewModel()

        vm.checkConnection(PROBE_SERVER_URL)

        assertEquals(ConnectionState.Connected(PROBE_SERVER_URL), vm.connectionState.value)
    }

    @Test
    fun `the probe is addressed to the typed server, not to the configured one`() {
        // An absolute URL, because nothing is configured yet and there is
        // therefore no base URL that would reach it.
        val vm = newViewModel()

        vm.checkConnection("tandem.example.com")

        coVerify(exactly = 1) { api.getHealth("https://tandem.example.com/api/health") }
    }

    @Test
    fun `an unreachable server maps to a readable failure, not an exception`() {
        coEvery { api.getHealth(any()) } throws IOException("failed to connect")

        val vm = newViewModel()
        vm.checkConnection(PROBE_SERVER_URL)

        val state = vm.connectionState.value
        assertTrue(state is ConnectionState.Failed)
        assertTrue((state as ConnectionState.Failed).message.isNotBlank())
    }

    @Test
    fun `an http error maps to a readable failure naming the status`() {
        coEvery { api.getHealth(any()) } throws HttpException(
            Response.error<Unit>(
                503,
                """{"status":"unhealthy"}""".toResponseBody("application/json".toMediaType()),
            ),
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
        coEvery { api.getHealth(any()) } throws HttpException(
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
        coEvery { api.getHealth(any()) } throws RuntimeException("Unexpected JSON token")

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
        coVerify(exactly = 0) { api.getHealth(any()) }
        coVerify(exactly = 0) { serverUrlManager.setServerUrl(any()) }
    }

    @Test
    fun `a server too old to report a status still counts as reachable`() {
        // The DTO's fields are nullable on purpose: a server predating the
        // version fields must not be reported as broken.
        coEvery { api.getHealth(any()) } returns HealthResponse()

        val vm = newViewModel()
        vm.checkConnection(PROBE_SERVER_URL)

        assertEquals(ConnectionState.Connected(PROBE_SERVER_URL), vm.connectionState.value)
    }

    @Test
    fun `re-checking clears the previous result`() {
        coEvery { api.getHealth(any()) } throws IOException("offline")
        val vm = newViewModel()
        vm.checkConnection(PROBE_SERVER_URL)
        assertTrue(vm.connectionState.value is ConnectionState.Failed)

        coEvery { api.getHealth(any()) } returns HealthResponse(status = "healthy")
        vm.checkConnection(PROBE_SERVER_URL)

        assertEquals(ConnectionState.Connected(PROBE_SERVER_URL), vm.connectionState.value)
    }

    // -- The probe stores nothing at all -----------------------------------
    //
    // Not "stores only what it verified" — nothing. Writing the URL is what
    // re-creates the login destination, and the destination owns this ViewModel:
    // the first fix stopped a *failed* probe from writing, and a successful one
    // then wiped the "Connected" message and the typed address the instant they
    // appeared. The write belongs to the user's next tap, where the re-creation
    // lands on the screen they were going to anyway.

    @Test
    fun `a failed probe does not store the address`() {
        coEvery { api.getHealth(any()) } throws IOException("Unable to resolve host")

        val vm = newViewModel()
        vm.checkConnection("nope.invalid")

        coVerify(exactly = 0) { serverUrlManager.setServerUrl(any()) }
    }

    @Test
    fun `a rejected probe does not store the address either`() {
        coEvery { api.getHealth(any()) } throws HttpException(
            Response.error<Unit>(500, "".toResponseBody(null)),
        )

        val vm = newViewModel()
        vm.checkConnection(PROBE_SERVER_URL)

        coVerify(exactly = 0) { serverUrlManager.setServerUrl(any()) }
    }

    @Test
    fun `a successful probe does not store the address either`() {
        val vm = newViewModel()

        vm.checkConnection(PROBE_SERVER_URL)

        coVerify(exactly = 0) { serverUrlManager.setServerUrl(any()) }
    }

    @Test
    fun `the verified address is carried on the Connected state, normalised`() {
        // Carried rather than re-read from the field when the user accepts: what
        // was verified is what gets stored, even if the text changed underneath.
        val vm = newViewModel()

        vm.checkConnection("  TANDEM.example.com  ")

        assertEquals(
            ConnectionState.Connected("https://tandem.example.com"),
            vm.connectionState.value,
        )
    }

    // -- Accepting the server ----------------------------------------------

    @Test
    fun `accepting stores the verified address exactly once and dismisses`() {
        val vm = newViewModel()
        vm.checkConnection("  TANDEM.example.com  ")

        vm.acceptServer()

        coVerify(exactly = 1) { serverUrlManager.setServerUrl("https://tandem.example.com") }
        assertFalse(vm.showFirstRun.value)
    }

    @Test
    fun `accepting before a successful check does nothing`() {
        val vm = newViewModel()

        vm.acceptServer()

        coVerify(exactly = 0) { serverUrlManager.setServerUrl(any()) }
        assertTrue(vm.showFirstRun.value)
    }

    @Test
    fun `accepting after a failed check does nothing`() {
        coEvery { api.getHealth(any()) } throws IOException("Unable to resolve host")
        val vm = newViewModel()
        vm.checkConnection("nope.invalid")

        vm.acceptServer()

        coVerify(exactly = 0) { serverUrlManager.setServerUrl(any()) }
        assertTrue(vm.showFirstRun.value)
    }

    @Test
    fun `a store the manager refuses is reported rather than silently dismissed`() {
        // normalizeServerUrl already passed, so this is close to unreachable —
        // but dismissing on a refused write would drop the user on a sign-in
        // form with no server, the exact state this screen exists to prevent.
        coEvery { serverUrlManager.setServerUrl(any()) } returns false
        val vm = newViewModel()
        vm.checkConnection(PROBE_SERVER_URL)

        vm.acceptServer()

        assertTrue(vm.connectionState.value is ConnectionState.Failed)
        assertTrue(vm.showFirstRun.value)
    }

    // -- The screen stays put ----------------------------------------------
    //
    // This state lives in an application-scoped gate rather than in the
    // composable, so it survives the destination being re-created when the URL
    // is finally stored — by which point the user is on their way out anyway.

    @Test
    fun `an unconfigured launch shows the first-run screen`() {
        assertTrue(newViewModel().showFirstRun.value)
    }

    @Test
    fun `a configured launch never shows it`() {
        every { serverUrlManager.currentUrl } returns PROBE_SERVER_URL
        every { serverUrlManager.serverUrlFlow } returns flowOf(PROBE_SERVER_URL)

        assertFalse(newViewModel().showFirstRun.value)
    }

    @Test
    fun `a failed probe leaves the first-run screen up`() {
        coEvery { api.getHealth(any()) } throws IOException("Unable to resolve host")

        val vm = newViewModel()
        vm.checkConnection("nope.invalid")

        // Otherwise the failure message is rendered on a screen nobody is
        // looking at, behind a password prompt for a server that does not exist.
        assertTrue(vm.showFirstRun.value)
    }

    @Test
    fun `a successful probe leaves the first-run screen up`() {
        val vm = newViewModel()

        vm.checkConnection(PROBE_SERVER_URL)

        // The "Connected" confirmation and the Continue button live there. On
        // the device this screen re-rendered blank the moment the probe stored
        // the URL: no message, no button, an empty address field.
        assertTrue(vm.showFirstRun.value)
    }

    @Test
    fun `skipping dismisses without storing anything`() {
        val vm = newViewModel()

        vm.dismissFirstRun()

        assertFalse(vm.showFirstRun.value)
        coVerify(exactly = 0) { serverUrlManager.setServerUrl(any()) }
    }

    @Test
    fun `dismissal survives a rebuilt ViewModel`() {
        // The destination is re-created when the stored URL changes; a fresh
        // LoginViewModel must not put the welcome screen back over the login form.
        val gate = FirstRunGate()
        val vm = newViewModel(gate)
        vm.checkConnection(PROBE_SERVER_URL)
        vm.acceptServer()

        every { serverUrlManager.currentUrl } returns PROBE_SERVER_URL
        assertFalse(newViewModel(gate).showFirstRun.value)
    }

    @Test
    fun `a rebuilt ViewModel mid-first-run still shows the screen`() {
        val gate = FirstRunGate()
        newViewModel(gate)

        // Nothing stores the URL mid-flow any more, but the latch is what makes
        // that safe rather than merely true today.
        every { serverUrlManager.currentUrl } returns PROBE_SERVER_URL
        assertTrue(newViewModel(gate).showFirstRun.value)
    }
}
