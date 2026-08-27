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
import com.booksync.util.restartApp
import io.mockk.coEvery
import io.mockk.every
import io.mockk.mockk
import io.mockk.mockkStatic
import io.mockk.unmockkStatic
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
 * Issue #149, the load-bearing half: **the app must not restart on a URL that was
 * refused.**
 *
 * `ServerUrlPolicyTest` and `ServerUrlManagerTest` pin the layer below this —
 * what normalizes, what persists — but neither can see the decision that actually
 * bricked installs. The original bug was not "a bad URL was stored"; it was
 * "a bad URL was stored *and then the process was killed*", which is what removed
 * every chance to correct it. A regression that dropped the `if` and always
 * restarted would leave every test in those two files green.
 *
 * `restartApp` is a top-level function, so it is stubbed with `mockkStatic` on its
 * generated file class. Note it cannot be observed by side effect instead: under
 * `unitTests.isReturnDefaultValues` the mocked `PackageManager` returns a null
 * launch intent and `restartApp` elects to return early.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class ServerUrlSaveGateTest {

    private lateinit var serverUrlManager: ServerUrlManager
    private lateinit var context: Context

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        mockkStatic("com.booksync.util.AppRestartKt")
        every { restartApp(any()) } returns Unit
        context = mockk(relaxed = true)

        serverUrlManager = mockk()
        every { serverUrlManager.currentUrl } returns "https://tandem.example.com"
        every { serverUrlManager.serverUrlFlow } returns flowOf("https://tandem.example.com")
    }

    @After
    fun tearDown() {
        unmockkStatic("com.booksync.util.AppRestartKt")
        Dispatchers.resetMain()
    }

    private fun loginViewModel(): LoginViewModel =
        LoginViewModel(mockk(relaxed = true), mockk(relaxed = true), serverUrlManager)

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
            dataStore, api, mockk<TokenManager>(relaxed = true),
            serverUrlManager, deviceIdManager, PasswordResetGate(), networkMonitor,
        )
    }

    // ---- login screen ----------------------------------------------------

    @Test
    fun `login screen does not restart when the url is refused`() {
        coEvery { serverUrlManager.setServerUrl(any()) } returns false
        val vm = loginViewModel()

        vm.saveServerUrlAndRestart(context, "not a url")

        verify(exactly = 0) { restartApp(any()) }
        assertNotNull("the refusal has to be visible", vm.error.value)
    }

    @Test
    fun `login screen restarts when the url is accepted`() {
        coEvery { serverUrlManager.setServerUrl(any()) } returns true
        val vm = loginViewModel()

        vm.saveServerUrlAndRestart(context, "tandem.example.com")

        verify(exactly = 1) { restartApp(context) }
        assertNull(vm.error.value)
    }

    // ---- account screen --------------------------------------------------

    @Test
    fun `account screen does not restart when the url is refused`() {
        coEvery { serverUrlManager.setServerUrl(any()) } returns false
        val vm = accountViewModel()

        vm.saveServerUrlAndRestart(context, "not a url")

        verify(exactly = 0) { restartApp(any()) }
        assertNotNull("the refusal has to be visible", vm.serverUrlError.value)
    }

    @Test
    fun `account screen restarts when the url is accepted`() {
        coEvery { serverUrlManager.setServerUrl(any()) } returns true
        val vm = accountViewModel()

        vm.saveServerUrlAndRestart(context, "tandem.example.com")

        verify(exactly = 1) { restartApp(context) }
        assertNull(vm.serverUrlError.value)
    }

    @Test
    fun `the account error clears once the user edits the field`() {
        coEvery { serverUrlManager.setServerUrl(any()) } returns false
        val vm = accountViewModel()

        vm.saveServerUrlAndRestart(context, "not a url")
        assertNotNull(vm.serverUrlError.value)

        vm.clearServerUrlError()

        assertEquals(null, vm.serverUrlError.value)
    }
}
