package com.booksync.ui.account

import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.emptyPreferences
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.DeviceIdManager
import com.booksync.data.remote.PasswordResetGate
import com.booksync.data.remote.ServerUrlManager
import com.booksync.data.remote.TokenManager
import com.booksync.data.remote.UserResponse
import com.booksync.data.util.NetworkMonitor
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.coVerifyOrder
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.After
import org.junit.Before
import org.junit.Test
import retrofit2.HttpException
import retrofit2.Response
import java.io.IOException

/**
 * Logout wiring (issue #82; the behavior landed untested in 13b6404 / #39).
 *
 * The contract that matters is *ordering plus unconditional cleanup*: revoke the
 * server-side tokens first — AuthInterceptor needs the still-stored bearer token to
 * authenticate that call — and clear the local tokens afterwards no matter what,
 * so an offline or already-expired session can still log out of the app.
 */
@OptIn(ExperimentalCoroutinesApi::class)
private const val TEST_SERVER_URL = "https://tandem.example.com"

class AccountViewModelLogoutTest {

    private lateinit var api: BookSyncApi
    private lateinit var tokenManager: TokenManager

    @Before
    fun setUp() {
        // viewModelScope dispatches on Main; Unconfined runs launched work eagerly,
        // so each test can assert immediately after calling logout().
        Dispatchers.setMain(UnconfinedTestDispatcher())

        api = mockk()
        tokenManager = mockk(relaxed = true)

        // init { loadProfile() } hits GET /api/auth/me on construction.
        coEvery { api.getMe() } returns UserResponse(
            id = 1,
            username = "claude",
            email = "claude@example.com",
            is_admin = false,
            is_active = true,
            created_at = "2026-01-01T00:00:00Z",
        )
        coEvery { api.logout() } returns Response.success(Unit)
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    private fun newViewModel(): AccountViewModel {
        val dataStore = mockk<DataStore<Preferences>>()
        every { dataStore.data } returns flowOf(emptyPreferences())

        val serverUrlManager = mockk<ServerUrlManager>()
        every { serverUrlManager.serverUrlFlow } returns flowOf(TEST_SERVER_URL)
        every { serverUrlManager.currentUrl } returns TEST_SERVER_URL

        val deviceIdManager = mockk<DeviceIdManager>()
        every { deviceIdManager.deviceNameFlow } returns flowOf("Test Device")
        every { deviceIdManager.deviceName } returns "Test Device"

        val networkMonitor = mockk<NetworkMonitor>()
        every { networkMonitor.isOnline } returns MutableStateFlow(true)

        return AccountViewModel(
            dataStore = dataStore,
            api = api,
            tokenManager = tokenManager,
            serverUrlManager = serverUrlManager,
            deviceIdManager = deviceIdManager,
            passwordResetGate = PasswordResetGate(),
            networkMonitor = networkMonitor,
            // Not exercised here — "Report a problem" (issue #230) is Android
            // plumbing (FileProvider, Intent); its payload is covered by
            // CrashReportFormatterTest.
            diagnosticLogger = mockk(relaxed = true),
            appContext = mockk(relaxed = true),
        )
    }

    @Test
    fun `logout calls the server revoke endpoint exactly once`() {
        newViewModel().logout()

        coVerify(exactly = 1) { api.logout() }
    }

    @Test
    fun `logout clears local tokens when the server call succeeds`() {
        newViewModel().logout()

        coVerify(exactly = 1) { tokenManager.clearTokens() }
    }

    @Test
    fun `logout still clears local tokens when the server is unreachable`() {
        coEvery { api.logout() } throws IOException("offline")

        newViewModel().logout()

        coVerify(exactly = 1) { tokenManager.clearTokens() }
    }

    @Test
    fun `logout still clears local tokens when the server rejects the token`() {
        coEvery { api.logout() } throws HttpException(
            Response.error<Unit>(401, "".toResponseBody(null)),
        )

        newViewModel().logout()

        coVerify(exactly = 1) { tokenManager.clearTokens() }
    }

    @Test
    fun `logout revokes on the server before clearing the local tokens`() {
        newViewModel().logout()

        // Clearing first would strip the bearer token AuthInterceptor attaches to
        // the revoke request, silently leaving the server-side token alive.
        coVerifyOrder {
            api.logout()
            tokenManager.clearTokens()
        }
    }
}
