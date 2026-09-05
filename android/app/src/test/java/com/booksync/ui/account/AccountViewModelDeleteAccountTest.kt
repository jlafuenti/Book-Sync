package com.booksync.ui.account

import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.emptyPreferences
import com.booksync.data.remote.AccountDeleteRequest
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
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import retrofit2.Response
import java.io.IOException

/**
 * Self-service account deletion (issue #146).
 *
 * Google Play requires an in-app deletion path for any app that can create an
 * account, and Tandem's register call is right there on the login screen.
 *
 * Two halves are pinned:
 *
 *  * **Success ends the session locally.** The server has destroyed the account,
 *    so every token this app holds is dead. Leaving them in place is the #143
 *    deadlock again — the next request 401s, the refresh 401s, and the app has
 *    no way back to a login form. Clearing them is what BookSyncNavigation
 *    observes to route to Login.
 *  * **A refusal keeps the session and says why, in the server's own words.**
 *    "Password is incorrect" and "you are the last active superadmin" are
 *    different problems with different fixes; HTTP 403/409 tells the user
 *    neither, which is exactly what `serverDetail()` exists for (issue #221).
 */
@OptIn(ExperimentalCoroutinesApi::class)
private const val TEST_SERVER_URL = "https://tandem.example.com"

private fun errorBody(json: String) =
    json.toResponseBody("application/json".toMediaTypeOrNull())

class AccountViewModelDeleteAccountTest {

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
        coEvery { api.deleteAccount(any()) } returns Response.success(null)
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

    // ---- Success ----------------------------------------------------------

    @Test
    fun `delete sends the typed password to the server`() {
        newViewModel().deleteAccount("hunter2")

        coVerify(exactly = 1) { api.deleteAccount(AccountDeleteRequest(password = "hunter2")) }
    }

    @Test
    fun `a successful delete clears the local tokens`() {
        val vm = newViewModel()
        vm.deleteAccount("hunter2")

        coVerify(exactly = 1) { tokenManager.clearTokens() }
        assertTrue(vm.deleteAccountState.value is DeleteAccountState.Success)
    }

    @Test
    fun `the account is deleted on the server before the tokens are cleared`() {
        newViewModel().deleteAccount("hunter2")

        // Clearing first would strip the bearer AuthInterceptor attaches, so the
        // request would 401 and the account would still exist.
        coVerifyOrder {
            api.deleteAccount(any())
            tokenManager.clearTokens()
        }
    }

    // ---- Refusals ---------------------------------------------------------

    @Test
    fun `a wrong password surfaces the server's own message and keeps the session`() {
        coEvery { api.deleteAccount(any()) } returns Response.error(
            403, errorBody("""{"detail":"Password is incorrect"}"""),
        )

        val vm = newViewModel()
        vm.deleteAccount("wrong")

        val state = vm.deleteAccountState.value
        assertTrue(state is DeleteAccountState.Error)
        assertEquals("Password is incorrect", (state as DeleteAccountState.Error).message)
        coVerify(exactly = 0) { tokenManager.clearTokens() }
    }

    @Test
    fun `the last-superadmin refusal is surfaced verbatim`() {
        val detail = "You are the last active superadmin. Promote another user " +
            "to superadmin first."
        coEvery { api.deleteAccount(any()) } returns Response.error(
            409, errorBody("""{"detail":"$detail"}"""),
        )

        val vm = newViewModel()
        vm.deleteAccount("hunter2")

        val state = vm.deleteAccountState.value
        assertTrue(state is DeleteAccountState.Error)
        // Nothing in the app can fix this — only the sentence explains the fix.
        assertEquals(detail, (state as DeleteAccountState.Error).message)
        coVerify(exactly = 0) { tokenManager.clearTokens() }
    }

    @Test
    fun `a refusal with no readable detail still reports the status`() {
        coEvery { api.deleteAccount(any()) } returns Response.error(
            500, errorBody("<html>gateway</html>"),
        )

        val vm = newViewModel()
        vm.deleteAccount("hunter2")

        val state = vm.deleteAccountState.value
        assertTrue(state is DeleteAccountState.Error)
        assertTrue((state as DeleteAccountState.Error).message.contains("500"))
        coVerify(exactly = 0) { tokenManager.clearTokens() }
    }

    @Test
    fun `an unreachable server keeps the tokens and reports the failure`() {
        coEvery { api.deleteAccount(any()) } throws IOException("offline")

        val vm = newViewModel()
        vm.deleteAccount("hunter2")

        assertTrue(vm.deleteAccountState.value is DeleteAccountState.Error)
        coVerify(exactly = 0) { tokenManager.clearTokens() }
    }

    @Test
    fun `resetting the state returns the dialog to idle`() {
        coEvery { api.deleteAccount(any()) } throws IOException("offline")

        val vm = newViewModel()
        vm.deleteAccount("hunter2")
        vm.resetDeleteAccountState()

        assertTrue(vm.deleteAccountState.value is DeleteAccountState.Idle)
    }
}
