package com.booksync.data.remote

import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.util.UUID

/**
 * DeviceIdManager provides a stable per-install device ID (persisted UUID) and a
 * friendly device name, used to attribute bookmark/progress writes to a device
 * (issue #54's multi-device conflict resolution contract).
 */
class DeviceIdManagerTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private val key = stringPreferencesKey("device_id")
    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())

    /** Seeds run here rather than on construction (issue #318). */
    private val seedScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    private fun newDataStore(): DataStore<Preferences> =
        PreferenceDataStoreFactory.create(scope = scope) {
            tmp.newFile("settings.preferences_pb")
        }

    @After
    fun tearDown() {
        scope.cancel()
    }

    @Test
    fun `deviceId is generated and matches UUID format when nothing stored`() {
        val manager = DeviceIdManager(newDataStore(), seedScope)

        // Will throw IllegalArgumentException if not a valid UUID string.
        val parsed = UUID.fromString(manager.deviceId)
        assertEquals(manager.deviceId, parsed.toString())
    }

    @Test
    fun `deviceId persists across repeated reads on the same instance`() {
        val manager = DeviceIdManager(newDataStore(), seedScope)

        val first = manager.deviceId
        val second = manager.deviceId

        assertEquals(first, second)
    }

    @Test
    fun `deviceId persists across separate manager instances backed by the same DataStore file`() {
        val dataStore = newDataStore()

        val firstManager = DeviceIdManager(dataStore, seedScope)
        val generatedId = firstManager.deviceId

        val secondManager = DeviceIdManager(dataStore, seedScope)

        assertEquals(generatedId, secondManager.deviceId)
    }

    @Test
    fun `generated deviceId is persisted to the underlying DataStore`() = runBlocking {
        val dataStore = newDataStore()
        val manager = DeviceIdManager(dataStore, seedScope)

        // Read the id first. Since issue #318 the seed runs off the constructor,
        // so it is persisted asynchronously just after construction or on first
        // read -- reading DataStore before touching the manager races the seed.
        // The contract that matters is unchanged: what the manager reports is
        // what is on disk, and only one id is ever generated.
        val reported = manager.deviceId
        val stored = dataStore.data.first()[key]

        assertEquals(reported, stored)
    }

    @Test
    fun `existing stored deviceId is reused rather than regenerated`() = runBlocking {
        val dataStore = newDataStore()
        val existing = UUID.randomUUID().toString()
        dataStore.edit { it[key] = existing }

        val manager = DeviceIdManager(dataStore, seedScope)

        assertEquals(existing, manager.deviceId)
    }

    @Test
    fun `deviceName is non-empty`() {
        val manager = DeviceIdManager(newDataStore(), seedScope)

        assertFalse(manager.deviceName.isBlank())
        assertTrue(manager.deviceName.isNotEmpty())
    }

    @Test
    fun `deviceName falls back to auto-derived value when no override is stored`() {
        val manager = DeviceIdManager(newDataStore(), seedScope)

        assertEquals(
            "${android.os.Build.MANUFACTURER} ${android.os.Build.MODEL}",
            manager.deviceName,
        )
    }

    @Test
    fun `setDeviceName persists an override readable by a second manager instance`() = runBlocking {
        val dataStore = newDataStore()
        val firstManager = DeviceIdManager(dataStore, seedScope)

        firstManager.setDeviceName("Kitchen Pixel")

        val secondManager = DeviceIdManager(dataStore, seedScope)
        assertEquals("Kitchen Pixel", secondManager.deviceName)
    }

    @Test
    fun `setDeviceName trims whitespace before persisting`() = runBlocking {
        val manager = DeviceIdManager(newDataStore(), seedScope)

        manager.setDeviceName("  Kitchen Pixel  ")

        assertEquals("Kitchen Pixel", manager.deviceName)
    }

    @Test
    fun `setDeviceName with null clears the override and reverts to auto-derived`() = runBlocking {
        val dataStore = newDataStore()
        val manager = DeviceIdManager(dataStore, seedScope)
        manager.setDeviceName("Kitchen Pixel")

        manager.setDeviceName(null)

        assertEquals(
            "${android.os.Build.MANUFACTURER} ${android.os.Build.MODEL}",
            manager.deviceName,
        )
        val overrideKey = stringPreferencesKey("device_name_override")
        assertEquals(null, dataStore.data.first()[overrideKey])
    }

    @Test
    fun `setDeviceName with blank string clears the override and reverts to auto-derived`() = runBlocking {
        val dataStore = newDataStore()
        val manager = DeviceIdManager(dataStore, seedScope)
        manager.setDeviceName("Kitchen Pixel")

        manager.setDeviceName("   ")

        assertEquals(
            "${android.os.Build.MANUFACTURER} ${android.os.Build.MODEL}",
            manager.deviceName,
        )
    }
}
