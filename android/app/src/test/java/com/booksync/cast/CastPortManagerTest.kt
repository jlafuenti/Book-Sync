package com.booksync.cast

import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
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

/** The stored cast port (issue #931). */
class CastPortManagerTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private val key = intPreferencesKey("cast_port")
    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())

    private fun newDataStore(): DataStore<Preferences> =
        PreferenceDataStoreFactory.create(scope = scope) { tmp.newFile("cast.preferences_pb") }

    @After
    fun tearDown() {
        scope.cancel()
    }

    @Test
    fun `a fresh install uses 8484`() = runBlocking {
        val manager = CastPortManager(newDataStore(), scope)
        assertEquals(8484, manager.currentPort)
        assertEquals(8484, manager.port.first())
    }

    @Test
    fun `a stored port wins over the default`() = runBlocking {
        val dataStore = newDataStore()
        dataStore.edit { it[key] = 9100 }
        val manager = CastPortManager(dataStore, scope)
        assertEquals(9100, manager.currentPort)
        assertEquals(9100, manager.port.first())
    }

    @Test
    fun `setPort stores a valid port and the very next read sees it`() = runBlocking {
        val dataStore = newDataStore()
        val manager = CastPortManager(dataStore, scope)
        assertTrue(manager.setPort(9200))
        assertEquals(9200, manager.currentPort)
        assertEquals(9200, manager.port.first())
        assertEquals(9200, dataStore.data.first()[key])
    }

    @Test
    fun `the bounds themselves are accepted`() = runBlocking {
        val manager = CastPortManager(newDataStore(), scope)
        assertTrue(manager.setPort(1024))
        assertTrue(manager.setPort(65535))
        assertEquals(65535, manager.currentPort)
    }

    @Test
    fun `a port outside 1024 to 65535 is refused and the old value is kept`() = runBlocking {
        val dataStore = newDataStore()
        val manager = CastPortManager(dataStore, scope)
        manager.setPort(9300)
        for (bad in listOf(0, 80, 1023, 65536, -5)) {
            assertFalse("$bad", manager.setPort(bad))
        }
        assertEquals(9300, manager.currentPort)
        assertEquals(9300, manager.port.first())
        assertEquals(9300, dataStore.data.first()[key])
    }

    @Test
    fun `a stored value outside the range reads back as the default`() = runBlocking {
        val dataStore = newDataStore()
        dataStore.edit { it[key] = 80 }
        val manager = CastPortManager(dataStore, scope)
        assertEquals(8484, manager.currentPort)
        assertEquals(8484, manager.port.first())
    }
}
