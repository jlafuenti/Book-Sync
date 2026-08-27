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
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

/**
 * The shipped default server URL is a build property (`tandem.defaultServerUrl`,
 * surfaced as `BuildConfig.DEFAULT_SERVER_URL`) that is injected here rather than
 * read from a compiled-in constant — which is what lets these tests cover the
 * empty-default case a clean clone actually builds with.
 *
 * The production server also moved from booksync.example.com (DNS record deleted)
 * to tandem.example.com, so a persisted legacy URL is migrated on construction;
 * a fresh install or app-data clear must never land on the dead hostname.
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
    fun `build default is used when nothing stored`() {
        val manager = ServerUrlManager(newDataStore(), "https://tandem.example.com")
        assertEquals("https://tandem.example.com", manager.currentUrl)
    }

    @Test
    fun `an empty build default leaves the server unconfigured`() {
        // A clean clone builds with no `tandem.defaultServerUrl`; nothing may be
        // invented on the user's behalf here.
        val manager = ServerUrlManager(newDataStore(), "")
        assertEquals("", manager.currentUrl)
    }

    @Test
    fun `stored legacy booksync url is migrated to the build default`() = runBlocking {
        val dataStore = newDataStore()
        dataStore.edit { it[key] = LEGACY_SERVER_URL }

        val manager = ServerUrlManager(dataStore, "https://tandem.example.com")

        assertEquals("https://tandem.example.com", manager.currentUrl)
        // The migration must also rewrite the persisted value, not just the cache.
        assertEquals("https://tandem.example.com", dataStore.data.first()[key])
    }

    @Test
    fun `stored custom url is left untouched`() = runBlocking {
        val dataStore = newDataStore()
        dataStore.edit { it[key] = "https://my.own.server:8443" }

        val manager = ServerUrlManager(dataStore, "https://tandem.example.com")

        assertEquals("https://my.own.server:8443", manager.currentUrl)
        assertEquals("https://my.own.server:8443", dataStore.data.first()[key])
    }

    @Test
    fun `the url flow falls back to the build default until one is stored`() = runBlocking {
        val dataStore = newDataStore()
        val manager = ServerUrlManager(dataStore, "https://tandem.example.com")

        assertEquals("https://tandem.example.com", manager.serverUrlFlow.first())

        manager.setServerUrl("https://my.own.server:8443/")

        assertEquals("https://my.own.server:8443", manager.serverUrlFlow.first())
        assertEquals("https://my.own.server:8443", manager.currentUrl)
    }

    @Test
    fun `an unusable url is refused and nothing is persisted`() = runBlocking {
        // Issue #149: this used to be stored verbatim, and the app then crashed
        // inside Hilt on every launch with reinstall as the only way out.
        val dataStore = newDataStore()
        val manager = ServerUrlManager(dataStore, "https://tandem.example.com")

        assertFalse(manager.setServerUrl("not a url"))

        assertEquals("https://tandem.example.com", manager.currentUrl)
        assertNull(dataStore.data.first()[key])
    }

    @Test
    fun `a refused url does not clobber an already-configured server`() = runBlocking {
        val dataStore = newDataStore()
        dataStore.edit { it[key] = "https://my.own.server:8443" }
        val manager = ServerUrlManager(dataStore, "https://tandem.example.com")

        assertFalse(manager.setServerUrl("ftp://nope"))

        assertEquals("https://my.own.server:8443", manager.currentUrl)
        assertEquals("https://my.own.server:8443", dataStore.data.first()[key])
    }

    @Test
    fun `a scheme-less host is normalised before it is persisted`() = runBlocking {
        val dataStore = newDataStore()
        val manager = ServerUrlManager(dataStore, "")

        assertTrue(manager.setServerUrl("tandem.example.com"))

        assertEquals("https://tandem.example.com", manager.currentUrl)
        assertEquals("https://tandem.example.com", dataStore.data.first()[key])
    }
}
