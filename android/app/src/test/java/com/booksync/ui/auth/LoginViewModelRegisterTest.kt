package com.booksync.ui.auth

import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.FirstRunGate
import com.booksync.data.remote.REGISTRATION_PENDING_MESSAGE
import com.booksync.data.remote.RegisterRequest
import com.booksync.data.remote.RegisterResponse
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
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import retrofit2.HttpException
import retrofit2.Response
import java.io.IOException

/**
 * "Request access" on the Android login screen (issue #221).
 *
 * The endpoint and the Retrofit declaration already existed — but the
 * declaration was wrong about the response and had never been exercised, so
 * "only UI wiring" turned out to be optimistic; see [RegisterWiringTest].
 *
 * What has to be pinned here is the ViewModel contract the screen drives: the
 * request is sent exactly once with what was typed, success shows a
 * pending-approval message and drops back to sign-in whatever the body looked
 * like, and a server that has registration switched off shows *its own* reason
 * rather than a generic failure — a 403 body of `{"detail": "Public
 * registration is disabled"}` is the only thing that tells the user not to keep
 * trying.
 *
 * Compose screens are excluded from coverage (no emulator or Robolectric in
 * CI), so these target [LoginViewModel] directly — same shape as
 * `ui/account/AccountViewModelLogoutTest`.
 */
private const val TEST_SERVER_URL = "https://tandem.example.com"

/** Verbatim from `server/routers/auth.py`; see RegisterWiringTest. */
private const val SERVER_PENDING_MESSAGE =
    "Access request submitted. An admin must approve your account before you can sign in."

@OptIn(ExperimentalCoroutinesApi::class)
class LoginViewModelRegisterTest {

    private lateinit var api: BookSyncApi
    private lateinit var tokenManager: TokenManager

    @Before
    fun setUp() {
        // viewModelScope dispatches on Main; Unconfined runs launched work
        // eagerly, so each test can assert immediately after the call.
        Dispatchers.setMain(UnconfinedTestDispatcher())

        api = mockk()
        tokenManager = mockk(relaxed = true)
        // What the server actually sends: a bare message, no user object. See
        // RegisterWiringTest for the decode contract this shape comes from.
        coEvery { api.register(any()) } returns RegisterResponse(message = SERVER_PENDING_MESSAGE)
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    private fun newViewModel(): LoginViewModel {
        val serverUrlManager = mockk<ServerUrlManager>()
        every { serverUrlManager.serverUrlFlow } returns flowOf(TEST_SERVER_URL)
        every { serverUrlManager.currentUrl } returns TEST_SERVER_URL

        return LoginViewModel(
            api = api,
            tokenManager = tokenManager,
            serverUrlManager = serverUrlManager,
            userScopeProvider = mockk<UserScopeProvider>(relaxed = true),
            firstRunGate = FirstRunGate(),
            serverVersionGate = com.booksync.data.remote.ServerVersionGate(api, serverUrlManager),
        )
    }

    private fun httpError(code: Int, body: String) = HttpException(
        Response.error<Unit>(code, body.toResponseBody("application/json".toMediaType())),
    )

    @Test
    fun `register posts exactly what was typed, once`() {
        newViewModel().register("newcomer", "newcomer@example.com", "hunter2")

        coVerify(exactly = 1) {
            api.register(RegisterRequest("newcomer", "newcomer@example.com", "hunter2"))
        }
    }

    @Test
    fun `a successful request shows the server's pending-approval message`() {
        val vm = newViewModel()

        vm.register("newcomer", "newcomer@example.com", "hunter2")

        // The account exists but cannot sign in yet; saying so is the whole
        // point of the flow. Preferring the server's own sentence mirrors
        // LoginPage.jsx, so an admin who customises it is heard on both clients.
        assertEquals(SERVER_PENDING_MESSAGE, vm.message.value)
        assertNull(vm.error.value)
    }

    @Test
    fun `a server that sends no message still confirms the request`() {
        // The 201 is the fact that matters; the sentence is decoration. A body
        // this app does not recognise must not read as a failure — that is the
        // bug this whole path was rewritten for.
        coEvery { api.register(any()) } returns RegisterResponse()

        val vm = newViewModel()
        vm.register("newcomer", "newcomer@example.com", "hunter2")

        assertEquals(REGISTRATION_PENDING_MESSAGE, vm.message.value)
        assertNull(vm.error.value)
        assertFalse(vm.isRegistering.value)
    }

    @Test
    fun `a blank message falls back rather than showing an empty banner`() {
        coEvery { api.register(any()) } returns RegisterResponse(message = "   ")

        val vm = newViewModel()
        vm.register("newcomer", "newcomer@example.com", "hunter2")

        assertEquals(REGISTRATION_PENDING_MESSAGE, vm.message.value)
    }

    @Test
    fun `isRegistering resets to false after a successful request`() {
        val vm = newViewModel()
        vm.setRegistering(true)
        assertTrue(vm.isRegistering.value)

        vm.register("newcomer", "newcomer@example.com", "hunter2")

        // Back on the sign-in form, where the message tells them to wait.
        assertFalse(vm.isRegistering.value)
    }

    @Test
    fun `a 403 surfaces the server's own detail and does not throw`() {
        coEvery { api.register(any()) } throws
            httpError(403, """{"detail":"Public registration is disabled"}""")

        val vm = newViewModel()
        vm.register("newcomer", "newcomer@example.com", "hunter2")

        assertEquals("Public registration is disabled", vm.error.value)
        assertNull(vm.message.value)
        // A rejected request must leave the form where it was, so the user can
        // read the reason next to the fields they filled in.
        assertFalse(vm.isLoading.value)
    }

    @Test
    fun `a 400 with a detail surfaces that detail too`() {
        coEvery { api.register(any()) } throws
            httpError(400, """{"detail":"Username already registered"}""")

        val vm = newViewModel()
        vm.register("taken", "taken@example.com", "hunter2")

        assertEquals("Username already registered", vm.error.value)
    }

    @Test
    fun `an error body without a usable detail still produces a message`() {
        // FastAPI validation errors put a list in `detail`, and a proxy in front
        // of the server may return HTML. Neither may crash or blank the card.
        coEvery { api.register(any()) } throws httpError(422, """{"detail":[{"msg":"bad"}]}""")

        val vm = newViewModel()
        vm.register("x", "x@example.com", "y")

        assertTrue(vm.error.value!!.isNotBlank())
    }

    @Test
    fun `an offline request reports a failure rather than throwing`() {
        coEvery { api.register(any()) } throws IOException("offline")

        val vm = newViewModel()
        vm.register("newcomer", "newcomer@example.com", "hunter2")

        assertTrue(vm.error.value!!.isNotBlank())
        assertFalse(vm.isLoading.value)
    }

    @Test
    fun `switching modes clears whatever the previous mode left on screen`() {
        coEvery { api.register(any()) } throws httpError(403, """{"detail":"nope"}""")
        val vm = newViewModel()
        vm.register("newcomer", "newcomer@example.com", "hunter2")
        assertEquals("nope", vm.error.value)

        vm.setRegistering(true)

        // A stale refusal from the other form is confusing, not informative.
        assertNull(vm.error.value)
        assertNull(vm.message.value)
    }
}
