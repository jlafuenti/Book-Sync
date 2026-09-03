package com.booksync.ui.auth

import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.DemoAccount
import com.booksync.data.remote.FirstRunGate
import com.booksync.data.remote.HealthResponse
import com.booksync.data.remote.LoginRequest
import com.booksync.data.remote.ServerUrlManager
import com.booksync.data.remote.ServerVersionGate
import com.booksync.data.remote.TokenManager
import com.booksync.data.remote.TokenResponse
import com.booksync.data.remote.UserResponse
import com.booksync.data.remote.UserScopeProvider
import com.booksync.data.remote.demoAccountOrNull
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
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import retrofit2.HttpException
import retrofit2.Response
import java.io.IOException

/**
 * "Try the demo" on the first-run screen (issue #147).
 *
 * Play's reviewer installs the app, sees an empty Server URL field, and has
 * nothing to type — the app is a client for a server the reviewer does not have.
 * Play calls that "app not functional". The button is the Grocy pattern: one tap
 * puts the reviewer (or a curious installer) inside a working library on the
 * public demo server, with no address and no credentials to copy across.
 *
 * The three values are build settings that default to empty, so a clean clone
 * ships no demo and no button — the same rule as `tandem.defaultServerUrl`
 * (issue #58). What this file pins is what the button does when they are set:
 *
 *  - it verifies `/api/health` **before** storing anything, exactly as
 *    "Check connection" does. A demo server that is down must not leave a
 *    stranger's install pointed at it;
 *  - it only leaves the welcome screen once the sign-in has actually
 *    succeeded. Dismissing first and failing after would strand the user on a
 *    password prompt for an account they were never given;
 *  - a refusal shows the server's own sentence, because "HTTP 401" does not
 *    distinguish a rotated demo password from a suspended demo account.
 */
private const val DEMO_URL = "https://demo.example.com"
private const val DEMO_USER = "playreview"
private const val DEMO_PASSWORD = "demo-password"

@OptIn(ExperimentalCoroutinesApi::class)
class LoginViewModelDemoTest {

    private lateinit var api: BookSyncApi
    private lateinit var serverUrlManager: ServerUrlManager
    private lateinit var tokenManager: TokenManager

    private val demo = DemoAccount(DEMO_URL, DEMO_USER, DEMO_PASSWORD)

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())

        api = mockk()
        serverUrlManager = mockk()
        tokenManager = mockk(relaxed = true)
        every { serverUrlManager.serverUrlFlow } returns flowOf("")
        every { serverUrlManager.currentUrl } returns ""
        coEvery { serverUrlManager.setServerUrl(any()) } returns true
        coEvery { api.getHealth(any()) } returns HealthResponse(status = "healthy")
        coEvery { api.login(any()) } returns TokenResponse("access", "refresh")
        coEvery { api.getMe() } returns UserResponse(
            id = 7,
            username = DEMO_USER,
            email = "demo@example.com",
            role = "user",
            is_admin = false,
            is_active = true,
            created_at = "2026-01-01T00:00:00Z",
        )
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    private fun newViewModel(
        demoAccount: DemoAccount? = demo,
        gate: FirstRunGate = FirstRunGate(),
    ) = LoginViewModel(
        api = api,
        tokenManager = tokenManager,
        serverUrlManager = serverUrlManager,
        userScopeProvider = mockk<UserScopeProvider>(relaxed = true),
        firstRunGate = gate,
        serverVersionGate = ServerVersionGate(api, serverUrlManager),
        demoAccount = demoAccount,
    )

    // -- Whether the button exists at all ----------------------------------

    @Test
    fun `a build with all three demo settings offers a demo account`() {
        assertEquals(
            DemoAccount(DEMO_URL, DEMO_USER, DEMO_PASSWORD),
            demoAccountOrNull(DEMO_URL, DEMO_USER, DEMO_PASSWORD),
        )
    }

    @Test
    fun `a clean clone, which sets none of them, offers nothing`() {
        assertNull(demoAccountOrNull("", "", ""))
    }

    @Test
    fun `a half-configured build offers nothing rather than a broken button`() {
        // Any one of the three missing makes the tap impossible to complete, and
        // a button that cannot work is worse than no button on the one screen a
        // brand-new install can reach.
        assertNull(demoAccountOrNull(DEMO_URL, DEMO_USER, ""))
        assertNull(demoAccountOrNull(DEMO_URL, "", DEMO_PASSWORD))
        assertNull(demoAccountOrNull("", DEMO_USER, DEMO_PASSWORD))
        assertNull(demoAccountOrNull("   ", DEMO_USER, DEMO_PASSWORD))
    }

    @Test
    fun `an unusable demo url offers nothing`() {
        // Same rule as every other address in the app: it goes through
        // normalizeServerUrl, or it does not get used.
        assertNull(demoAccountOrNull("https:/typo", DEMO_USER, DEMO_PASSWORD))
    }

    @Test
    fun `the demo url is normalised once, at build-settings level`() {
        assertEquals(
            DemoAccount("https://demo.example.com", DEMO_USER, DEMO_PASSWORD),
            demoAccountOrNull("  DEMO.example.com/  ", DEMO_USER, DEMO_PASSWORD),
        )
    }

    @Test
    fun `the screen is told there is no demo when the build ships none`() {
        assertNull(newViewModel(demoAccount = null).demoAccount)
    }

    @Test
    fun `a demo tap on a build with no demo does nothing at all`() {
        var landed = false
        val vm = newViewModel(demoAccount = null)

        vm.signInToDemo { landed = true }

        assertFalse(landed)
        coVerify(exactly = 0) { api.getHealth(any()) }
        coVerify(exactly = 0) { serverUrlManager.setServerUrl(any()) }
    }

    // -- The happy path ----------------------------------------------------

    @Test
    fun `the demo tap stores the demo server, signs in and leaves first-run`() {
        var landed = false
        val vm = newViewModel()

        vm.signInToDemo { landed = true }

        coVerify(exactly = 1) { serverUrlManager.setServerUrl(DEMO_URL) }
        coVerify(exactly = 1) { api.login(LoginRequest(DEMO_USER, DEMO_PASSWORD)) }
        coVerify(exactly = 1) { tokenManager.saveTokens("access", "refresh") }
        assertFalse(vm.showFirstRun.value)
        assertTrue(landed)
        assertNull(vm.error.value)
        assertFalse(vm.isLoading.value)
    }

    @Test
    fun `the health probe runs before the address is stored`() {
        val vm = newViewModel()

        vm.signInToDemo {}

        // Absolute URL, bypassing the base-URL interceptor: nothing is
        // configured yet, so there is no base URL that would reach it.
        coVerify(exactly = 1) { api.getHealth("$DEMO_URL/api/health") }
    }

    @Test
    fun `the demo role is learned before any screen renders`() {
        // Same reason as the ordinary sign-in (issue #170): a screen that
        // renders before the role is known treats the user as having none.
        val vm = newViewModel()

        vm.signInToDemo {}

        coVerify(exactly = 1) { tokenManager.saveRole("user") }
    }

    // -- The demo server is down -------------------------------------------

    @Test
    fun `an unreachable demo server leaves first-run up and stores nothing`() {
        coEvery { api.getHealth(any()) } throws IOException("Unable to resolve host")
        var landed = false
        val vm = newViewModel()

        vm.signInToDemo { landed = true }

        coVerify(exactly = 0) { serverUrlManager.setServerUrl(any()) }
        coVerify(exactly = 0) { api.login(any()) }
        assertTrue(vm.showFirstRun.value)
        assertFalse(landed)
        assertNotNull(vm.error.value)
        assertFalse(vm.isLoading.value)
    }

    @Test
    fun `a demo host answering something that is not Tandem stores nothing either`() {
        coEvery { api.getHealth(any()) } throws HttpException(
            Response.error<Unit>(404, "".toResponseBody(null)),
        )
        val vm = newViewModel()

        vm.signInToDemo {}

        coVerify(exactly = 0) { serverUrlManager.setServerUrl(any()) }
        assertTrue(vm.showFirstRun.value)
        assertNotNull(vm.error.value)
    }

    // -- The demo credentials stopped working ------------------------------

    @Test
    fun `a refused demo sign-in shows the server's own sentence`() {
        coEvery { api.login(any()) } throws HttpException(
            Response.error<Unit>(
                401,
                """{"detail":"Account is inactive"}"""
                    .toResponseBody("application/json".toMediaType()),
            ),
        )
        var landed = false
        val vm = newViewModel()

        vm.signInToDemo { landed = true }

        assertEquals("Account is inactive", vm.error.value)
        assertFalse(landed)
        // The welcome screen is where the message belongs: the user never had
        // credentials of their own, so a password prompt is not the next step.
        assertTrue(vm.showFirstRun.value)
        assertFalse(vm.isLoading.value)
    }

    @Test
    fun `a refusal with no readable body still says something`() {
        coEvery { api.login(any()) } throws HttpException(
            Response.error<Unit>(503, "".toResponseBody(null)),
        )
        val vm = newViewModel()

        vm.signInToDemo {}

        assertTrue(vm.error.value.orEmpty().contains("503"))
        assertTrue(vm.showFirstRun.value)
    }

    @Test
    fun `a network failure during sign-in is a sentence, not a crash`() {
        coEvery { api.login(any()) } throws IOException("connection reset")
        val vm = newViewModel()

        vm.signInToDemo {}

        assertTrue(vm.error.value.orEmpty().isNotBlank())
        assertTrue(vm.showFirstRun.value)
        assertFalse(vm.isLoading.value)
    }

    @Test
    fun `a store the manager refuses stops before any credentials are sent`() {
        coEvery { serverUrlManager.setServerUrl(any()) } returns false
        val vm = newViewModel()

        vm.signInToDemo {}

        coVerify(exactly = 0) { api.login(any()) }
        assertNotNull(vm.error.value)
        assertTrue(vm.showFirstRun.value)
    }

    @Test
    fun `a failed demo tap can be followed by a normal check connection`() {
        // The error banner must not outlive the attempt it describes, or the
        // next screen state looks like it has already failed.
        coEvery { api.getHealth(any()) } throws IOException("offline")
        val vm = newViewModel()
        vm.signInToDemo {}
        assertNotNull(vm.error.value)

        vm.clearError()

        assertNull(vm.error.value)
    }
}
