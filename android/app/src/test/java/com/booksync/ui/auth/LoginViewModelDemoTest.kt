package com.booksync.ui.auth

import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.DemoAccount
import com.booksync.data.remote.DemoSignIn
import com.booksync.data.remote.DemoSignInState
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
import io.mockk.coVerifyOrder
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.yield
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
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
 * nothing to type — the app is a client for a server they do not have. Play calls
 * that "app not functional". The button is the Grocy pattern: one tap puts the
 * reviewer (or a curious installer) inside a working library on the public demo
 * server, with no address and no credentials to copy across.
 *
 * The three values are build settings that default to empty, so a clean clone
 * ships no demo and no button. What this file pins is what the tap does when they
 * are set — and in particular the two things that broke it on a device:
 *
 *  - **The flow does not live on the ViewModel.** The first cut ran it in
 *    `viewModelScope`. On the emulator `/api/health` answered 200, the login
 *    answered 200, and then nothing happened at all: storing the server URL
 *    re-created the login destination, which cancelled the coroutine holding the
 *    rest of the flow — no tokens, no dismissal, no navigation, and no error,
 *    because a cancelled coroutine reports nothing. `the sign-in completes even
 *    though the ViewModel's own scope never runs` is that bug.
 *  - **Nothing is stored until the credentials are accepted.** The probe and the
 *    sign-in both go to an absolute URL, so the disruptive write happens once,
 *    after everything that can ordinarily fail.
 */
private const val DEMO_URL = "https://demo.example.com"
private const val DEMO_USER = "playreview"
private const val DEMO_PASSWORD = "demo-password"

@OptIn(ExperimentalCoroutinesApi::class)
class LoginViewModelDemoTest {

    private lateinit var api: BookSyncApi
    private lateinit var serverUrlManager: ServerUrlManager
    private lateinit var tokenManager: TokenManager
    private lateinit var userScopeProvider: UserScopeProvider

    private val demo = DemoAccount(DEMO_URL, DEMO_USER, DEMO_PASSWORD)

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())

        api = mockk()
        serverUrlManager = mockk()
        tokenManager = mockk(relaxed = true)
        userScopeProvider = mockk(relaxed = true)
        every { serverUrlManager.serverUrlFlow } returns flowOf("")
        every { serverUrlManager.currentUrl } returns ""
        coEvery { serverUrlManager.setServerUrl(any()) } returns true
        coEvery { api.getHealth(any()) } returns HealthResponse(status = "healthy")
        coEvery { api.loginAt(any(), any()) } returns TokenResponse("access", "refresh")
        coEvery { api.getMeAt(any()) } returns UserResponse(
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

    private fun newDemoSignIn(
        gate: FirstRunGate,
        scope: CoroutineScope,
    ) = DemoSignIn(
        api = api,
        tokenManager = tokenManager,
        serverUrlManager = serverUrlManager,
        userScopeProvider = userScopeProvider,
        firstRunGate = gate,
        serverVersionGate = ServerVersionGate(api, serverUrlManager),
        scope = scope,
    )

    private fun newViewModel(
        demoAccount: DemoAccount? = demo,
        gate: FirstRunGate = FirstRunGate(),
        scope: CoroutineScope = CoroutineScope(UnconfinedTestDispatcher()),
    ) = LoginViewModel(
        api = api,
        tokenManager = tokenManager,
        serverUrlManager = serverUrlManager,
        userScopeProvider = userScopeProvider,
        firstRunGate = gate,
        serverVersionGate = ServerVersionGate(api, serverUrlManager),
        demoAccount = demoAccount,
        demoSignIn = demoAccount?.let { newDemoSignIn(gate, scope) },
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
        val vm = newViewModel(demoAccount = null)

        vm.signInToDemo()

        assertEquals(DemoSignInState.Idle, vm.demoState.value)
        coVerify(exactly = 0) { api.getHealth(any()) }
        coVerify(exactly = 0) { serverUrlManager.setServerUrl(any()) }
    }

    // -- The happy path ----------------------------------------------------

    @Test
    fun `the demo tap signs in, stores the demo server and leaves first-run`() {
        val vm = newViewModel()

        vm.signInToDemo()

        coVerify(exactly = 1) { api.loginAt("$DEMO_URL/api/auth/login", LoginRequest(DEMO_USER, DEMO_PASSWORD)) }
        coVerify(exactly = 1) { tokenManager.saveTokens("access", "refresh") }
        coVerify(exactly = 1) { serverUrlManager.setServerUrl(DEMO_URL) }
        coVerify(exactly = 1) { userScopeProvider.onAuthenticated() }
        assertEquals(DemoSignInState.Succeeded, vm.demoState.value)
        assertFalse(vm.showFirstRun.value)
    }

    @Test
    fun `every demo request carries the demo address itself`() {
        // Absolute URLs, bypassing the base-URL interceptor: nothing is
        // configured yet, so there is no base URL that would reach the demo host,
        // and the whole point is not to configure one until the login lands.
        val vm = newViewModel()

        vm.signInToDemo()

        coVerify(exactly = 1) { api.getHealth("$DEMO_URL/api/health") }
        coVerify(exactly = 1) { api.loginAt("$DEMO_URL/api/auth/login", any()) }
        coVerify(exactly = 1) { api.getMeAt("$DEMO_URL/api/auth/me") }
    }

    @Test
    fun `nothing is stored until the credentials have been accepted`() {
        // The ordering is the fix for the device bug: setServerUrl is what
        // re-creates the login destination, so it must not sit between two
        // requests that can fail.
        val vm = newViewModel()

        vm.signInToDemo()

        coVerifyOrder {
            api.getHealth(any())
            api.loginAt(any(), any())
            serverUrlManager.setServerUrl(DEMO_URL)
            tokenManager.saveTokens(any(), any())
        }
    }

    @Test
    fun `the demo role is learned before any screen renders`() {
        // Same reason as the ordinary sign-in (issue #170): a screen that renders
        // before the role is known treats the user as having none.
        val vm = newViewModel()

        vm.signInToDemo()

        coVerify(exactly = 1) { tokenManager.saveRole("user") }
    }

    @Test
    fun `a second tap while the first is still running is ignored`() {
        val scope = TestScope(StandardTestDispatcher())
        val vm = newViewModel(scope = scope)

        vm.signInToDemo()
        vm.signInToDemo()
        scope.advanceUntilIdle()

        coVerify(exactly = 1) { api.loginAt(any(), any()) }
    }

    // -- The bug this design exists for ------------------------------------

    @Test
    fun `the sign-in completes even though the ViewModel's own scope never runs`() {
        // The regression test for the device failure. `Dispatchers.Main` here is a
        // StandardTestDispatcher that is never advanced, so anything launched in
        // viewModelScope simply does not execute — which is what cancellation
        // looked like on the emulator: the login answered 200 and every step after
        // it vanished. The demo scope runs; the ViewModel's does not.
        Dispatchers.setMain(StandardTestDispatcher())
        val gate = FirstRunGate()
        val vm = newViewModel(gate = gate, scope = CoroutineScope(UnconfinedTestDispatcher()))

        vm.signInToDemo()

        coVerify(exactly = 1) { tokenManager.saveTokens("access", "refresh") }
        coVerify(exactly = 1) { serverUrlManager.setServerUrl(DEMO_URL) }
        assertTrue("the welcome screen was never dismissed", gate.dismissed.value)
        assertEquals(DemoSignInState.Succeeded, vm.demoState.value)
    }

    @Test
    fun `a ViewModel rebuilt mid-flight sees the result the old one started`() {
        // The screen reads the singleton's StateFlow directly, so the result lands
        // on whatever composition is alive when it arrives — not on the one that
        // started it, which by then may be gone.
        val gate = FirstRunGate()
        val scope = TestScope(StandardTestDispatcher())
        val signIn = newDemoSignIn(gate, scope)

        LoginViewModel(
            api = api,
            tokenManager = tokenManager,
            serverUrlManager = serverUrlManager,
            userScopeProvider = userScopeProvider,
            firstRunGate = gate,
            serverVersionGate = ServerVersionGate(api, serverUrlManager),
            demoAccount = demo,
            demoSignIn = signIn,
        ).signInToDemo()

        val rebuilt = LoginViewModel(
            api = api,
            tokenManager = tokenManager,
            serverUrlManager = serverUrlManager,
            userScopeProvider = userScopeProvider,
            firstRunGate = gate,
            serverVersionGate = ServerVersionGate(api, serverUrlManager),
            demoAccount = demo,
            demoSignIn = signIn,
        )
        scope.advanceUntilIdle()

        assertEquals(DemoSignInState.Succeeded, rebuilt.demoState.value)
    }

    @Test
    fun `consuming the result stops the screen navigating again`() {
        val vm = newViewModel()
        vm.signInToDemo()
        assertEquals(DemoSignInState.Succeeded, vm.demoState.value)

        vm.consumeDemoResult()

        assertEquals(DemoSignInState.Idle, vm.demoState.value)
    }

    // -- Ordering, because the screen is torn down by its own success ------

    @Test
    fun `success is published before the welcome screen is dismissed`() {
        // The device bug: dismissing first flips showFirstRun, which swaps
        // FirstRunScreen for the sign-in form. Announcing the success after that
        // told a composable that no longer existed, and the app sat on a username
        // box with a valid session behind it. The screen now watches from
        // LoginScreen, which survives the swap, and this keeps the order that
        // stops the sign-in form flashing up before the navigation lands.
        val gate = FirstRunGate()
        val order = mutableListOf<String>()
        val vm = newViewModel(gate = gate)

        val watcher = CoroutineScope(UnconfinedTestDispatcher())
        watcher.launch { vm.demoState.collect { if (it is DemoSignInState.Succeeded) order += "succeeded" } }
        watcher.launch { gate.dismissed.collect { if (it) order += "dismissed" } }

        vm.signInToDemo()
        watcher.cancel()

        assertEquals(listOf("succeeded", "dismissed"), order)
    }

    @Test
    fun `the server url write completes before the session is saved`() {
        // Not just "is called before" — completes. The session is keyed on
        // (server, user), so a token saved while the URL write is still in flight
        // would be scoped to whatever the server used to be.
        val order = mutableListOf<String>()
        coEvery { serverUrlManager.setServerUrl(any()) } coAnswers {
            order += "setServerUrl:start"
            yield()
            order += "setServerUrl:done"
            true
        }
        coEvery { tokenManager.saveTokens(any(), any()) } coAnswers { order += "saveTokens" }
        coEvery { userScopeProvider.onAuthenticated() } coAnswers { order += "onAuthenticated" }

        newViewModel().signInToDemo()

        assertEquals(
            listOf("setServerUrl:start", "setServerUrl:done", "saveTokens", "onAuthenticated"),
            order,
        )
    }

    @Test
    fun `the demo flow touches local storage exactly once, to store the server`() {
        // There is no cache reset to race — nothing in the app deletes or clears
        // the database on a server change (DatabaseResetGuardTest pins that), and
        // this pins that the demo flow does not invent one.
        val vm = newViewModel()

        vm.signInToDemo()

        coVerify(exactly = 1) { serverUrlManager.setServerUrl(any()) }
        coVerify(exactly = 1) { userScopeProvider.onAuthenticated() }
    }

    // -- The demo server is down -------------------------------------------

    @Test
    fun `an unreachable demo server leaves first-run up and stores nothing`() {
        coEvery { api.getHealth(any()) } throws IOException("Unable to resolve host")
        val vm = newViewModel()

        vm.signInToDemo()

        coVerify(exactly = 0) { serverUrlManager.setServerUrl(any()) }
        coVerify(exactly = 0) { api.loginAt(any(), any()) }
        assertTrue(vm.showFirstRun.value)
        assertTrue(vm.demoState.value is DemoSignInState.Failed)
    }

    @Test
    fun `a demo host answering something that is not Tandem stores nothing either`() {
        coEvery { api.getHealth(any()) } throws HttpException(
            Response.error<Unit>(404, "".toResponseBody(null)),
        )
        val vm = newViewModel()

        vm.signInToDemo()

        coVerify(exactly = 0) { serverUrlManager.setServerUrl(any()) }
        assertTrue(vm.showFirstRun.value)
        assertTrue(vm.demoState.value is DemoSignInState.Failed)
    }

    // -- The demo credentials stopped working ------------------------------

    @Test
    fun `a refused demo sign-in shows the server's own sentence and stores nothing`() {
        coEvery { api.loginAt(any(), any()) } throws HttpException(
            Response.error<Unit>(
                401,
                """{"detail":"Account is inactive"}"""
                    .toResponseBody("application/json".toMediaType()),
            ),
        )
        val vm = newViewModel()

        vm.signInToDemo()

        assertEquals(
            DemoSignInState.Failed("Account is inactive"),
            vm.demoState.value,
        )
        // Refusing the credentials must not leave the install pointed at the demo:
        // the address is only worth storing once it has proved useful.
        coVerify(exactly = 0) { serverUrlManager.setServerUrl(any()) }
        coVerify(exactly = 0) { tokenManager.saveTokens(any(), any()) }
        // The welcome screen is where the message belongs: the user never had
        // credentials of their own, so a password prompt is not the next step.
        assertTrue(vm.showFirstRun.value)
    }

    @Test
    fun `a refusal with no readable body still says something`() {
        coEvery { api.loginAt(any(), any()) } throws HttpException(
            Response.error<Unit>(503, "".toResponseBody(null)),
        )
        val vm = newViewModel()

        vm.signInToDemo()

        val state = vm.demoState.value
        assertTrue(state is DemoSignInState.Failed)
        assertTrue((state as DemoSignInState.Failed).message.contains("503"))
    }

    @Test
    fun `a network failure during sign-in is a sentence, not a crash`() {
        coEvery { api.loginAt(any(), any()) } throws IOException("connection reset")
        val vm = newViewModel()

        vm.signInToDemo()

        val state = vm.demoState.value
        assertTrue(state is DemoSignInState.Failed)
        assertTrue((state as DemoSignInState.Failed).message.isNotBlank())
        assertTrue(vm.showFirstRun.value)
    }

    @Test
    fun `a store the manager refuses is reported rather than silently succeeding`() {
        // Close to unreachable — the URL was normalized when the build settings
        // were read — but a session with no server to spend it on is worse than a
        // message.
        coEvery { serverUrlManager.setServerUrl(any()) } returns false
        val vm = newViewModel()

        vm.signInToDemo()

        coVerify(exactly = 0) { tokenManager.saveTokens(any(), any()) }
        assertTrue(vm.demoState.value is DemoSignInState.Failed)
        assertTrue(vm.showFirstRun.value)
    }

    @Test
    fun `a role lookup that fails does not undo a sign-in that worked`() {
        // Best-effort, as on the ordinary sign-in path: the role simply stays
        // unknown, which hasMinRole treats as no permission.
        coEvery { api.getMeAt(any()) } throws IOException("flaky")
        val vm = newViewModel()

        vm.signInToDemo()

        assertEquals(DemoSignInState.Succeeded, vm.demoState.value)
        assertFalse(vm.showFirstRun.value)
    }
}
