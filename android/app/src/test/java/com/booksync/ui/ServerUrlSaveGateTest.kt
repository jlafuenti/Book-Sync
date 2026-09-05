package com.booksync.ui

import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.emptyPreferences
import android.content.Context
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.DeviceIdManager
import com.booksync.data.remote.PasswordResetGate
import com.booksync.data.remote.ServerUrlManager
import com.booksync.data.remote.TokenManager
import com.booksync.data.remote.UserResponse
import com.booksync.data.util.NetworkMonitor
import com.booksync.ui.account.AccountViewModel
import com.booksync.ui.auth.LoginViewModel
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test

/**
 * Issue #149, the load-bearing half: **the app must not act on a URL that was
 * refused.**
 *
 * The action used to be a process restart. Since issue #228 there is none —
 * BaseUrlInterceptor reads the URL per request — and what a refused URL must not
 * trigger is the session teardown that an accepted one does. The gate is the
 * same; only what sits behind it changed.
 *
 * `ServerUrlPolicyTest` and `ServerUrlManagerTest` pin the layer below this —
 * what normalizes, what persists — but neither can see the decision that actually
 * bricked installs. The original bug was not "a bad URL was stored"; it was
 * "a bad URL was stored *and then the process was killed*", which is what removed
 * every chance to correct it. A regression that dropped the `if` and always
 * restarted would leave every test in those two files green.
 *
 * The observable effect is now `tokenManager.clearTokens()` on the account screen
 * (the login screen has no session to clear), so these read it from a relaxed mock
 * rather than stubbing a top-level function.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class ServerUrlSaveGateTest {

    private lateinit var serverUrlManager: ServerUrlManager
    private lateinit var tokenManager: TokenManager
    private lateinit var context: Context

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        context = mockk(relaxed = true)
        tokenManager = mockk(relaxed = true)

        serverUrlManager = mockk()
        every { serverUrlManager.currentUrl } returns "https://tandem.example.com"
        every { serverUrlManager.serverUrlFlow } returns flowOf("https://tandem.example.com")
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    private fun loginViewModel(): LoginViewModel =
        LoginViewModel(
            mockk(relaxed = true),
            mockk(relaxed = true),
            serverUrlManager,
            mockk(relaxed = true),
            com.booksync.data.remote.FirstRunGate(),
            com.booksync.data.remote.ServerVersionGate(mockk(relaxed = true), serverUrlManager),
            mockk(relaxed = true),
        )

    private fun accountViewModel(): AccountViewModel {
        val api = mockk<BookSyncApi>()
        coEvery { api.getMe() } returns UserResponse(
            id = 1,
            username = "claude",
            email = "claude@example.com",
            is_admin = false,
            is_active = true,
            created_at = "2026-01-01T00:00:00Z",
        )
        val dataStore = mockk<DataStore<Preferences>>()
        every { dataStore.data } returns flowOf(emptyPreferences())
        val deviceIdManager = mockk<DeviceIdManager>(relaxed = true)
        every { deviceIdManager.deviceNameFlow } returns flowOf("test-device")
        every { deviceIdManager.deviceName } returns "test-device"
        val networkMonitor = mockk<NetworkMonitor>()
        every { networkMonitor.isOnline } returns MutableStateFlow(true)
        return AccountViewModel(
            dataStore, api, tokenManager,
            serverUrlManager, deviceIdManager, PasswordResetGate(), networkMonitor,
            // "Report a problem" (issue #230) is not what this gate is about.
            mockk(relaxed = true), mockk(relaxed = true),
        )
    }

    // ---- login screen ----------------------------------------------------

    @Test
    fun `login screen does not accept a refused url`() {
        coEvery { serverUrlManager.setServerUrl(any()) } returns false
        val vm = loginViewModel()

        vm.saveServerUrl("not a url")

        assertNotNull("the refusal has to be visible", vm.error.value)
    }

    @Test
    fun `login screen accepts a valid url without error`() {
        coEvery { serverUrlManager.setServerUrl(any()) } returns true
        val vm = loginViewModel()

        vm.saveServerUrl("tandem.example.com")

        assertNull(vm.error.value)
    }

    // ---- account screen --------------------------------------------------

    @Test
    fun `account screen keeps the session when the url is refused`() {
        coEvery { serverUrlManager.setServerUrl(any()) } returns false
        val vm = accountViewModel()

        vm.saveServerUrl("not a url")

        coVerify(exactly = 0) { tokenManager.clearTokens() }
        assertNotNull("the refusal has to be visible", vm.serverUrlError.value)
    }

    @Test
    fun `account screen ends the session when the url is accepted`() {
        coEvery { serverUrlManager.setServerUrl(any()) } returns true
        val vm = accountViewModel()

        vm.saveServerUrl("tandem.example.com")

        coVerify(exactly = 1) { tokenManager.clearTokens() }
        assertNull(vm.serverUrlError.value)
    }

    @Test
    fun `the account error clears once the user edits the field`() {
        coEvery { serverUrlManager.setServerUrl(any()) } returns false
        val vm = accountViewModel()

        vm.saveServerUrl("not a url")
        assertNotNull(vm.serverUrlError.value)

        vm.clearServerUrlError()

        assertEquals(null, vm.serverUrlError.value)
    }
}
