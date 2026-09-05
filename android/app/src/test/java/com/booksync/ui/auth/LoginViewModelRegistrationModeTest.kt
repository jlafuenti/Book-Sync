package com.booksync.ui.auth

import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.FirstRunGate
import com.booksync.data.remote.RegisterRequest
import com.booksync.data.remote.RegisterResponse
import com.booksync.data.remote.RegistrationMode
import com.booksync.data.remote.RegistrationModeResponse
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
import org.junit.Before
import org.junit.Test
import retrofit2.HttpException
import retrofit2.Response
import java.io.IOException

/**
 * The Android half of issue #210.
 *
 * The sign-in screen offered "Request Access" to every server, including ones
 * that take no requests — so the only way to find out was to fill the form in
 * and read a 403. It now asks `GET /api/auth/registration` first.
 *
 * The failure behaviour is the interesting part and is deliberately optimistic:
 * an app pointed at a server too old to have the endpoint, or one that is
 * briefly unreachable, must still offer the request form. Hiding it on an error
 * would be a client that cannot recover from a transient 502.
 */
private const val TEST_SERVER_URL = "https://tandem.example.com"

@OptIn(ExperimentalCoroutinesApi::class)
class LoginViewModelRegistrationModeTest {

    private lateinit var api: BookSyncApi

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        api = mockk()
        coEvery { api.register(any()) } returns RegisterResponse(message = "ok")
        coEvery { api.getRegistrationMode() } returns RegistrationModeResponse("open")
    }

    @After
    fun tearDown() = Dispatchers.resetMain()

    private fun newViewModel(serverUrl: String = TEST_SERVER_URL): LoginViewModel {
        val serverUrlManager = mockk<ServerUrlManager>()
        every { serverUrlManager.serverUrlFlow } returns flowOf(serverUrl)
        every { serverUrlManager.currentUrl } returns serverUrl

        return LoginViewModel(
            api = api,
            tokenManager = mockk<TokenManager>(relaxed = true),
            serverUrlManager = serverUrlManager,
            userScopeProvider = mockk<UserScopeProvider>(relaxed = true),
            firstRunGate = FirstRunGate(),
            serverVersionGate = com.booksync.data.remote.ServerVersionGate(api, serverUrlManager),
            deviceIdManager = mockk(relaxed = true),
        )
    }

    @Test
    fun `an open server keeps the request form`() {
        assertEquals(RegistrationMode.OPEN, newViewModel().registrationMode.value)
    }

    @Test
    fun `an invite server reports invite`() {
        coEvery { api.getRegistrationMode() } returns RegistrationModeResponse("invite")

        assertEquals(RegistrationMode.INVITE, newViewModel().registrationMode.value)
    }

    @Test
    fun `a closed server reports closed`() {
        coEvery { api.getRegistrationMode() } returns RegistrationModeResponse("closed")

        assertEquals(RegistrationMode.CLOSED, newViewModel().registrationMode.value)
    }

    @Test
    fun `a server without the endpoint still offers the request form`() {
        coEvery { api.getRegistrationMode() } throws HttpException(
            Response.error<Unit>(404, "".toResponseBody("application/json".toMediaType())),
        )

        assertEquals(RegistrationMode.OPEN, newViewModel().registrationMode.value)
    }

    @Test
    fun `an unreachable server still offers the request form`() {
        coEvery { api.getRegistrationMode() } throws IOException("offline")

        assertEquals(RegistrationMode.OPEN, newViewModel().registrationMode.value)
    }

    @Test
    fun `a mode this build does not recognise is treated as open`() {
        coEvery { api.getRegistrationMode() } returns RegistrationModeResponse("semi-open")

        assertEquals(RegistrationMode.OPEN, newViewModel().registrationMode.value)
    }

    @Test
    fun `nothing is asked when no server is configured yet`() {
        val vm = newViewModel(serverUrl = "")

        coVerify(exactly = 0) { api.getRegistrationMode() }
        assertEquals(RegistrationMode.OPEN, vm.registrationMode.value)
    }

    @Test
    fun `saving a server re-reads the mode from that server`() {
        val serverUrlManager = mockk<ServerUrlManager>()
        every { serverUrlManager.serverUrlFlow } returns flowOf(TEST_SERVER_URL)
        every { serverUrlManager.currentUrl } returns TEST_SERVER_URL
        coEvery { serverUrlManager.setServerUrl(any()) } returns true

        val vm = LoginViewModel(
            api = api,
            tokenManager = mockk<TokenManager>(relaxed = true),
            serverUrlManager = serverUrlManager,
            userScopeProvider = mockk<UserScopeProvider>(relaxed = true),
            firstRunGate = FirstRunGate(),
            serverVersionGate = com.booksync.data.remote.ServerVersionGate(api, serverUrlManager),
            deviceIdManager = mockk(relaxed = true),
        )
        coEvery { api.getRegistrationMode() } returns RegistrationModeResponse("closed")

        vm.saveServerUrl("https://other.example.com")

        assertEquals(RegistrationMode.CLOSED, vm.registrationMode.value)
    }

    @Test
    fun `an invite code travels with the request`() {
        coEvery { api.getRegistrationMode() } returns RegistrationModeResponse("invite")
        val vm = newViewModel()

        vm.register("newcomer", "newcomer@example.com", "hunter22", "CODE-1")

        coVerify(exactly = 1) {
            api.register(
                RegisterRequest("newcomer", "newcomer@example.com", "hunter22", "CODE-1"),
            )
        }
    }

    @Test
    fun `a blank code is left out of the body entirely`() {
        // An `open` server must see exactly the body it has always seen.
        val vm = newViewModel()

        vm.register("newcomer", "newcomer@example.com", "hunter22", "  ")

        coVerify(exactly = 1) {
            api.register(RegisterRequest("newcomer", "newcomer@example.com", "hunter22", null))
        }
    }
}
