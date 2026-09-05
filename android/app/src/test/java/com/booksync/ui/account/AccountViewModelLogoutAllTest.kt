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
 * "Sign out everywhere" (issue #250).
 *
 * #250 made logout per-device so that signing out of the browser would stop
 * signing the phone out mid-book. That left `POST /api/auth/logout-all` with no
 * client at all — reachable only by curl, per docs/operations.md — even though
 * the case it exists for (a lost device) is the user's, not the operator's.
 *
 * The contract is the same as plain logout, and that sameness is the point: hit
 * the server first, because AuthInterceptor needs the still-stored bearer token
 * to authenticate the revoke, and clear the local tokens afterwards *whatever*
 * came back. A device left signed in because the network was down is precisely
 * the state this button exists to end.
 */
@OptIn(ExperimentalCoroutinesApi::class)
private const val TEST_SERVER_URL = "https://tandem.example.com"

class AccountViewModelLogoutAllTest {

    private lateinit var api: BookSyncApi
    private lateinit var tokenManager: TokenManager

    @Before
    fun setUp() {
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
        coEvery { api.logoutAll() } returns Response.success(Unit)
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
            // Issue #230's share action needs a real Android context and is not
            // what this test is about.
            diagnosticLogger = mockk(relaxed = true),
            appContext = mockk(relaxed = true),
        )
    }

    @Test
    fun `logoutAll calls the account-wide endpoint exactly once`() {
        newViewModel().logoutAll()

        coVerify(exactly = 1) { api.logoutAll() }
    }

    @Test
    fun `logoutAll does not call the per-device endpoint`() {
        // The two are not interchangeable: /logout revokes one session and
        // leaves the lost device signed in, which is the whole failure mode.
        newViewModel().logoutAll()

        coVerify(exactly = 0) { api.logout() }
    }

    @Test
    fun `logoutAll clears local tokens when the server call succeeds`() {
        newViewModel().logoutAll()

        coVerify(exactly = 1) { tokenManager.clearTokens() }
    }

    @Test
    fun `logoutAll still clears local tokens when the server is unreachable`() {
        coEvery { api.logoutAll() } throws IOException("offline")

        newViewModel().logoutAll()

        coVerify(exactly = 1) { tokenManager.clearTokens() }
    }

    @Test
    fun `logoutAll still clears local tokens when the server rejects the token`() {
        coEvery { api.logoutAll() } throws HttpException(
            Response.error<Unit>(401, "".toResponseBody(null)),
        )

        newViewModel().logoutAll()

        coVerify(exactly = 1) { tokenManager.clearTokens() }
    }

    @Test
    fun `logoutAll revokes on the server before clearing the local tokens`() {
        newViewModel().logoutAll()

        // Clearing first strips the bearer token AuthInterceptor attaches, and
        // the revoke that never happened leaves every other device signed in.
        coVerifyOrder {
            api.logoutAll()
            tokenManager.clearTokens()
        }
    }
}
