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
        val manager = DeviceIdManager(newDataStore())

        // Will throw IllegalArgumentException if not a valid UUID string.
        val parsed = UUID.fromString(manager.deviceId)
        assertEquals(manager.deviceId, parsed.toString())
    }

    @Test
    fun `deviceId persists across repeated reads on the same instance`() {
        val manager = DeviceIdManager(newDataStore())

        val first = manager.deviceId
        val second = manager.deviceId

        assertEquals(first, second)
    }

    @Test
    fun `deviceId persists across separate manager instances backed by the same DataStore file`() {
        val dataStore = newDataStore()

        val firstManager = DeviceIdManager(dataStore)
        val generatedId = firstManager.deviceId

        val secondManager = DeviceIdManager(dataStore)

        assertEquals(generatedId, secondManager.deviceId)
    }

    @Test
    fun `generated deviceId is persisted to the underlying DataStore`() = runBlocking {
        val dataStore = newDataStore()
        val manager = DeviceIdManager(dataStore)

        val stored = dataStore.data.first()[key]

        assertEquals(manager.deviceId, stored)
    }

    @Test
    fun `existing stored deviceId is reused rather than regenerated`() = runBlocking {
        val dataStore = newDataStore()
        val existing = UUID.randomUUID().toString()
        dataStore.edit { it[key] = existing }

        val manager = DeviceIdManager(dataStore)

        assertEquals(existing, manager.deviceId)
    }

    @Test
    fun `deviceName is non-empty`() {
        val manager = DeviceIdManager(newDataStore())

        assertFalse(manager.deviceName.isBlank())
        assertTrue(manager.deviceName.isNotEmpty())
    }
}
