package com.booksync.ui.account

import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.emptyPreferences
import com.booksync.data.local.dao.SyncMapStorageStats
import com.booksync.data.local.dao.SyncPointDao
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.DeviceIdManager
import com.booksync.data.remote.PasswordResetGate
import com.booksync.data.remote.ServerUrlManager
import com.booksync.data.remote.TokenManager
import com.booksync.data.repository.BookSyncRepository
import com.booksync.data.repository.SyncMapRemovalStore
import com.booksync.data.util.NetworkMonitor
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import java.io.IOException

/**
 * Account → Storage's "Sync data — N books, about X MB" line and its "Clear"
 * button (issue #678).
 */
@OptIn(ExperimentalCoroutinesApi::class)
class AccountViewModelSyncMapStorageTest {

    private val syncPointDao = mockk<SyncPointDao>(relaxed = true)
    private val repository = mockk<BookSyncRepository>(relaxed = true)
    private val syncMapRemovalStore = mockk<SyncMapRemovalStore>(relaxed = true)

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        // Baseline so every test's `AccountViewModel` construction — which
        // calls this in `init` — has something to return; tests that care
        // about a specific value re-stub after this, and MockK's later
        // registration wins.
        coEvery { syncPointDao.storageStats() } returns
            SyncMapStorageStats(pairCount = 0, pointCount = 0, textPreviewBytes = 0)
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    private fun mutableDataStore(): DataStore<Preferences> {
        var current = emptyPreferences()
        val dataStore = mockk<DataStore<Preferences>>()
        every { dataStore.data } answers { flowOf(current) }
        coEvery { dataStore.updateData(any()) } coAnswers {
            @Suppress("UNCHECKED_CAST")
            val transform = it.invocation.args[0] as suspend (Preferences) -> Preferences
            current = transform(current)
            current
        }
        return dataStore
    }

    private fun newViewModel(): AccountViewModel {
        val api = mockk<BookSyncApi>()
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
            dataStore = mutableDataStore(),
            api = api,
            tokenManager = mockk<TokenManager>(relaxed = true),
            serverUrlManager = serverUrlManager,
            deviceIdManager = deviceIdManager,
            passwordResetGate = PasswordResetGate(),
            networkMonitor = networkMonitor,
            diagnosticLogger = mockk(relaxed = true),
            appContext = mockk(relaxed = true),
            repository = repository,
            syncPointDao = syncPointDao,
            syncMapRemovalStore = syncMapRemovalStore,
        )
    }

    @Test
    fun `storage info reflects the DAO's stats on load`() = runTest {
        coEvery { syncPointDao.storageStats() } returns
            SyncMapStorageStats(pairCount = 3, pointCount = 900, textPreviewBytes = 45_000)

        val viewModel = newViewModel()

        val stats = viewModel.syncMapStorage.first()
        assertEquals(3, stats.pairCount)
        assertEquals(45_000L + 900L * 28L, stats.approxBytes)
    }

    @Test
    fun `clear-all empties the cache and the storage line reports 0 books`() = runTest {
        coEvery { syncPointDao.distinctPairIds() } returns listOf(1, 2, 3)
        // Before the clear: 3 books cached. After: the DAO reports empty —
        // clearAllSyncMaps re-reads the stats once it has cleared everything.
        coEvery { syncPointDao.storageStats() } returnsMany listOf(
            SyncMapStorageStats(pairCount = 3, pointCount = 300, textPreviewBytes = 9_000),
            SyncMapStorageStats(pairCount = 0, pointCount = 0, textPreviewBytes = 0),
        )

        val viewModel = newViewModel()
        assertEquals(3, viewModel.syncMapStorage.first().pairCount)

        viewModel.clearAllSyncMaps()

        assertEquals(0, viewModel.syncMapStorage.first().pairCount)
    }

    @Test
    fun `clear-all clears every cached pair's sync map`() = runTest {
        coEvery { syncPointDao.distinctPairIds() } returns listOf(1, 2, 3)

        newViewModel().clearAllSyncMaps()

        coVerify(exactly = 1) { repository.clearSyncMapCache(1) }
        coVerify(exactly = 1) { repository.clearSyncMapCache(2) }
        coVerify(exactly = 1) { repository.clearSyncMapCache(3) }
    }

    @Test
    fun `clear-all marks every cleared pair as removed by hand`() = runTest {
        coEvery { syncPointDao.distinctPairIds() } returns listOf(1, 2, 3)

        newViewModel().clearAllSyncMaps()

        coVerify(exactly = 1) { syncMapRemovalStore.markRemoved(setOf(1, 2, 3)) }
    }

    @Test
    fun `clear-all with nothing cached does nothing destructive`() = runTest {
        coEvery { syncPointDao.distinctPairIds() } returns emptyList()

        newViewModel().clearAllSyncMaps()

        coVerify(exactly = 0) { repository.clearSyncMapCache(any()) }
    }
}
