package com.booksync.ui.account

import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.emptyPreferences
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.DeviceIdManager
import com.booksync.data.remote.INVALID_SERVER_URL_MESSAGE
import com.booksync.data.remote.PasswordResetGate
import com.booksync.data.remote.ServerUrlManager
import com.booksync.data.remote.TokenManager
import com.booksync.data.remote.UserResponse
import com.booksync.data.util.NetworkMonitor
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test

/**
 * Switching servers ends the session, and does not kill the process (issue #228).
 *
 * Two things were wrong. The app called `Runtime.getRuntime().exit(0)` because
 * Retrofit's base URL was fixed at construction — and the position-sync contract
 * requires the final flush to run in an application-scoped, non-cancellable
 * coroutine, which `exit(0)` does not wait for. So changing servers could discard
 * a reading position.
 *
 * And the tokens survived the switch, so server A's bearer was sent to server B —
 * a host that may belong to someone else entirely — until B rejected it.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class AccountViewModelServerSwitchTest {

    private lateinit var api: BookSyncApi
    private lateinit var tokenManager: TokenManager
    private lateinit var serverUrlManager: ServerUrlManager

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        api = mockk()
        tokenManager = mockk(relaxed = true)
        serverUrlManager = mockk()
        every { serverUrlManager.serverUrlFlow } returns flowOf("https://old.example.com")
        every { serverUrlManager.currentUrl } returns "https://old.example.com"
        coEvery { api.getMe() } returns UserResponse(
            id = 1, username = "claude", email = "claude@example.com",
            is_admin = false, is_active = true, created_at = "2026-01-01T00:00:00Z",
        )
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    private fun newViewModel(): AccountViewModel {
        val dataStore = mockk<DataStore<Preferences>>()
        every { dataStore.data } returns flowOf(emptyPreferences())

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
            // See AccountViewModelLogoutTest — issue #230's share action needs a
            // real Android context and is not what this test is about.
            diagnosticLogger = mockk(relaxed = true),
            appContext = mockk(relaxed = true),
        )
    }

    @Test
    fun `accepting a new server URL clears the session`() {
        coEvery { serverUrlManager.setServerUrl("https://new.example.com") } returns true

        newViewModel().saveServerUrl("https://new.example.com")

        // Otherwise the next request carries the old server's bearer to the new
        // host, and on its 401 the refresh token follows it.
        coVerify(exactly = 1) { tokenManager.clearTokens() }
    }

    @Test
    fun `a rejected URL changes nothing`() {
        // Issue #149: the app used to store the raw string and restart, which is
        // how it bricked itself. Refusing must leave the session alone too.
        coEvery { serverUrlManager.setServerUrl("http://") } returns false

        val vm = newViewModel()
        vm.saveServerUrl("http://")

        coVerify(exactly = 0) { tokenManager.clearTokens() }
        assertEquals(INVALID_SERVER_URL_MESSAGE, vm.serverUrlError.value)
    }
}
