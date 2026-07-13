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
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

/**
 * The production server moved from booksync.example.com (DNS record deleted)
 * to tandem.example.com. These tests pin the new default and the one-time
 * migration of a persisted legacy URL, so a fresh install or app-data clear
 * never lands on the dead hostname again.
 */
class ServerUrlManagerTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private val key = stringPreferencesKey("server_url")
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
    fun `default url is tandem when nothing stored`() {
        val manager = ServerUrlManager(newDataStore())
        assertEquals("https://tandem.example.com", manager.currentUrl)
    }

    @Test
    fun `stored legacy booksync url is migrated to tandem`() = runBlocking {
        val dataStore = newDataStore()
        dataStore.edit { it[key] = "https://booksync.example.com" }

        val manager = ServerUrlManager(dataStore)

        assertEquals("https://tandem.example.com", manager.currentUrl)
        // The migration must also rewrite the persisted value, not just the cache.
        assertEquals("https://tandem.example.com", dataStore.data.first()[key])
    }

    @Test
    fun `stored custom url is left untouched`() = runBlocking {
        val dataStore = newDataStore()
        dataStore.edit { it[key] = "https://my.own.server:8443" }

        val manager = ServerUrlManager(dataStore)

        assertEquals("https://my.own.server:8443", manager.currentUrl)
        assertEquals("https://my.own.server:8443", dataStore.data.first()[key])
    }
}
