package com.booksync.ui.account

import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.emptyPreferences
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.DeviceIdManager
import com.booksync.data.remote.PasswordResetGate
import com.booksync.data.remote.ServerUrlManager
import com.booksync.data.remote.TokenManager
import com.booksync.data.util.NetworkMonitor
import io.mockk.coEvery
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test
import java.io.IOException

/**
 * The two per-device sync-map prefetch settings (issue #655): "Download sync
 * maps with the ebook" and "Only download sync maps over Wi-Fi". Both default
 * on, both persist through the same `booksync_prefs` DataStore the existing
 * auto-cleanup toggles use, and `DownloadWorker` reads the same two keys
 * directly — see `SyncMapAutoFetchTest` for the fetch decision itself.
 *
 * Uses a mocked [DataStore] that actually applies `edit`'s transform to an
 * in-memory [Preferences] (same idiom as `AccountViewModelLogoutTest`'s
 * `dataStore.data` stub, extended to make `updateData` — what the `edit`
 * extension calls — do real work). A genuine file-backed DataStore was tried
 * first and is flaky here: `AccountViewModel`'s setters fire the write via an
 * un-awaited `viewModelScope.launch`, and a real DataStore's write lands on
 * its own actor thread, so nothing in the test forces that real cross-thread
 * completion to happen before the assertion runs. The mock has no such gap —
 * `coEvery { updateData(...) }` runs inline under `UnconfinedTestDispatcher`.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class AccountViewModelSyncMapSettingsTest {

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    /** A [DataStore] whose `edit` actually mutates an in-memory [Preferences]. */
    private fun mutableDataStore(initial: Preferences = emptyPreferences()): Pair<DataStore<Preferences>, () -> Preferences> {
        var current = initial
        val dataStore = mockk<DataStore<Preferences>>()
        every { dataStore.data } answers { flowOf(current) }
        coEvery { dataStore.updateData(any()) } coAnswers {
            @Suppress("UNCHECKED_CAST")
            val transform = it.invocation.args[0] as suspend (Preferences) -> Preferences
            current = transform(current)
            current
        }
        return dataStore to { current }
    }

    private fun newViewModel(dataStore: DataStore<Preferences>): AccountViewModel {
        val api = mockk<BookSyncApi>()
        // init { loadProfile() } calls this; the failure is caught and stored
        // as _userError, which none of these tests read.
        coEvery { api.getMe() } throws IOException("not exercised in this test")

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
            tokenManager = mockk<TokenManager>(relaxed = true),
            serverUrlManager = serverUrlManager,
            deviceIdManager = deviceIdManager,
            passwordResetGate = PasswordResetGate(),
            networkMonitor = networkMonitor,
            diagnosticLogger = mockk(relaxed = true),
            appContext = mockk(relaxed = true),
            repository = mockk(relaxed = true),
            syncPointDao = mockk(relaxed = true),
            syncMapRemovalStore = mockk(relaxed = true),
        )
    }

    @Test
    fun `sync map with ebook defaults on for a fresh install`() = runBlocking {
        val (dataStore, _) = mutableDataStore()
        assertEquals(true, newViewModel(dataStore).syncMapWithEbook.first())
    }

    @Test
    fun `sync map wifi-only defaults on for a fresh install`() = runBlocking {
        val (dataStore, _) = mutableDataStore()
        assertEquals(true, newViewModel(dataStore).syncMapWifiOnly.first())
    }

    @Test
    fun `setSyncMapWithEbook persists false through the DataStore`() = runBlocking {
        val (dataStore, current) = mutableDataStore()

        newViewModel(dataStore).setSyncMapWithEbook(false)

        assertEquals(false, current()[SYNC_MAP_WITH_EBOOK])
        // A fresh instance reads the same persisted value back.
        assertEquals(false, newViewModel(dataStore).syncMapWithEbook.first())
    }

    @Test
    fun `setSyncMapWifiOnly persists false through the DataStore`() = runBlocking {
        val (dataStore, current) = mutableDataStore()

        newViewModel(dataStore).setSyncMapWifiOnly(false)

        assertEquals(false, current()[SYNC_MAP_WIFI_ONLY])
        assertEquals(false, newViewModel(dataStore).syncMapWifiOnly.first())
    }

    @Test
    fun `the two settings are independent`() = runBlocking {
        val (dataStore, current) = mutableDataStore()
        val viewModel = newViewModel(dataStore)

        viewModel.setSyncMapWithEbook(false)

        assertEquals(false, current()[SYNC_MAP_WITH_EBOOK])
        assertNull(current()[SYNC_MAP_WIFI_ONLY]) // untouched — still defaults to on when read
        assertEquals(true, newViewModel(dataStore).syncMapWifiOnly.first())
    }
}
