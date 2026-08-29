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
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import retrofit2.Response

/**
 * Changing your password ends the Android session (issue #143).
 *
 * `change_password` bumps `token_version` server-side, so the moment it returns
 * 200 the tokens still held here are dead. The app used to keep them and report
 * success: the next request 401'd, the refresh 401'd too, and — before the
 * authenticator rewrite — the whole process deadlocked. This is the everyday
 * route into that bug, Account → Change password, and the issue does not mention
 * it.
 *
 * Clearing belongs here rather than at the navigation layer because both screens
 * that change a password share this view-model, and only one of them had a
 * navigation callback doing the clear.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class AccountViewModelChangePasswordTest {

    private lateinit var api: BookSyncApi
    private lateinit var tokenManager: TokenManager

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        api = mockk()
        tokenManager = mockk(relaxed = true)
        coEvery { api.getMe() } returns UserResponse(
            id = 1,
            username = "claude",
            email = "claude@example.com",
            is_admin = false,
            is_active = true,
            created_at = "2026-01-01T00:00:00Z",
        )
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    private fun newViewModel(): AccountViewModel {
        val dataStore = mockk<DataStore<Preferences>>()
        every { dataStore.data } returns flowOf(emptyPreferences())

        val serverUrlManager = mockk<ServerUrlManager>()
        every { serverUrlManager.serverUrlFlow } returns flowOf("https://tandem.example.com")
        every { serverUrlManager.currentUrl } returns "https://tandem.example.com"

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
        )
    }

    @Test
    fun `a successful change ends the session exactly once`() {
        coEvery { api.changePassword(any()) } returns Response.success(Unit)

        newViewModel().changePassword("old-pw", "new-pw")

        // Exactly once: getAccessToken() has no distinctUntilChanged, so a second
        // clear is a second null emission and a second navigate to login.
        coVerify(exactly = 1) { tokenManager.clearTokens() }
    }

    @Test
    fun `a successful change still reports success`() {
        coEvery { api.changePassword(any()) } returns Response.success(Unit)

        val vm = newViewModel()
        vm.changePassword("old-pw", "new-pw")

        assertTrue(vm.changePasswordState.value is ChangePasswordState.Success)
    }

    @Test
    fun `a rejected change keeps the session`() {
        // A wrong current password is answered 400. Signing the user out for a
        // typo would be its own bug.
        coEvery { api.changePassword(any()) } returns
            Response.error(400, "".toResponseBody(null))

        newViewModel().changePassword("wrong-pw", "new-pw")

        coVerify(exactly = 0) { tokenManager.clearTokens() }
    }

    @Test
    fun `a network failure keeps the session`() {
        coEvery { api.changePassword(any()) } throws java.io.IOException("offline")

        newViewModel().changePassword("old-pw", "new-pw")

        coVerify(exactly = 0) { tokenManager.clearTokens() }
    }
}
