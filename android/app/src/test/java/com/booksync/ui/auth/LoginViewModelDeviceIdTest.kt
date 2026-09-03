package com.booksync.ui.auth

import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.DeviceIdManager
import com.booksync.data.remote.FirstRunGate
import com.booksync.data.remote.LoginRequest
import com.booksync.data.remote.ServerUrlManager
import com.booksync.data.remote.ServerVersionGate
import com.booksync.data.remote.TokenManager
import com.booksync.data.remote.TokenResponse
import com.booksync.data.remote.UserResponse
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
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test

/**
 * Sign-in names the device (issue #250).
 *
 * The server used to invalidate every token the account held whenever anyone
 * logged out, so signing out in the browser signed the phone out too — and the
 * phone is the device carrying reading positions it has not pushed yet, whose
 * 15-minute sweep then 401s until somebody notices. Sessions are per-device now,
 * and this is the client's half of that: the login says which device it is, so
 * the session it opens can be ended on its own.
 *
 * The same id attributes bookmark and progress writes ([DeviceIdManager]), which
 * is deliberate — the server should not have two disagreeing ideas of what a
 * device is.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class LoginViewModelDeviceIdTest {

    private val serverUrl = "https://tandem.example.com"
    private lateinit var api: BookSyncApi

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        api = mockk()
        coEvery { api.login(any()) } returns TokenResponse(
            access_token = "access", refresh_token = "refresh",
        )
        coEvery { api.getMe() } returns UserResponse(
            id = 1,
            username = "jesse",
            email = "jesse@example.com",
            role = "user",
            is_admin = false,
            is_active = true,
            created_at = "2026-09-03T00:00:00",
        )
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    private fun newViewModel(deviceId: String): LoginViewModel {
        val serverUrlManager = mockk<ServerUrlManager>()
        every { serverUrlManager.serverUrlFlow } returns flowOf(serverUrl)
        every { serverUrlManager.currentUrl } returns serverUrl

        val deviceIdManager = mockk<DeviceIdManager>()
        every { deviceIdManager.deviceId } returns deviceId

        return LoginViewModel(
            api = api,
            tokenManager = mockk<TokenManager>(relaxed = true),
            serverUrlManager = serverUrlManager,
            userScopeProvider = mockk<UserScopeProvider>(relaxed = true),
            firstRunGate = FirstRunGate(),
            serverVersionGate = ServerVersionGate(api, serverUrlManager),
            deviceIdManager = deviceIdManager,
        )
    }

    @Test
    fun `login sends this install's device id`() {
        var succeeded = false

        newViewModel("device-abc").login("jesse", "pw") { succeeded = true }

        coVerify(exactly = 1) {
            api.login(LoginRequest("jesse", "pw", device_id = "device-abc"))
        }
        assertEquals(true, succeeded)
    }
}
