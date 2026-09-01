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
 * The production server also moved from booksync.lafuenti.com (DNS record deleted)
 * to tandem.lafuenti.com, so a persisted legacy URL is migrated on construction;
 * a fresh install or app-data clear must never land on the dead hostname.
 */
class ServerUrlManagerTest {

    /** Seeds run off the constructor since issue #318. */
    private val seedScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)


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
        val manager = ServerUrlManager(newDataStore(), "https://tandem.example.com", seedScope)
        assertEquals("https://tandem.example.com", manager.currentUrl)
    }

    @Test
    fun `an empty build default leaves the server unconfigured`() {
        // A clean clone builds with no `tandem.defaultServerUrl`; nothing may be
        // invented on the user's behalf here.
        val manager = ServerUrlManager(newDataStore(), "", seedScope)
        assertEquals("", manager.currentUrl)
    }

    @Test
    fun `stored legacy booksync url is migrated to the build default`() = runBlocking {
        val dataStore = newDataStore()
        dataStore.edit { it[key] = LEGACY_SERVER_URL }

        val manager = ServerUrlManager(dataStore, "https://tandem.example.com", seedScope)

        assertEquals("https://tandem.example.com", manager.currentUrl)
        // The migration must also rewrite the persisted value, not just the cache.
        assertEquals("https://tandem.example.com", dataStore.data.first()[key])
    }

    @Test
    fun `stored custom url is left untouched`() = runBlocking {
        val dataStore = newDataStore()
        dataStore.edit { it[key] = "https://my.own.server:8443" }

        val manager = ServerUrlManager(dataStore, "https://tandem.example.com", seedScope)

        assertEquals("https://my.own.server:8443", manager.currentUrl)
        assertEquals("https://my.own.server:8443", dataStore.data.first()[key])
    }

    @Test
    fun `the url flow falls back to the build default until one is stored`() = runBlocking {
        val dataStore = newDataStore()
        val manager = ServerUrlManager(dataStore, "https://tandem.example.com", seedScope)

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
        val manager = ServerUrlManager(dataStore, "https://tandem.example.com", seedScope)

        assertFalse(manager.setServerUrl("not a url"))

        assertEquals("https://tandem.example.com", manager.currentUrl)
        assertNull(dataStore.data.first()[key])
    }

    @Test
    fun `a refused url does not clobber an already-configured server`() = runBlocking {
        val dataStore = newDataStore()
        dataStore.edit { it[key] = "https://my.own.server:8443" }
        val manager = ServerUrlManager(dataStore, "https://tandem.example.com", seedScope)

        assertFalse(manager.setServerUrl("ftp://nope"))

        assertEquals("https://my.own.server:8443", manager.currentUrl)
        assertEquals("https://my.own.server:8443", dataStore.data.first()[key])
    }

    @Test
    fun `a scheme-less host is normalised before it is persisted`() = runBlocking {
        val dataStore = newDataStore()
        val manager = ServerUrlManager(dataStore, "", seedScope)

        assertTrue(manager.setServerUrl("tandem.example.com"))

        assertEquals("https://tandem.example.com", manager.currentUrl)
        assertEquals("https://tandem.example.com", dataStore.data.first()[key])
    }

    // -- Repair on read (issue #149 review finding) -------------------------
    //
    // setServerUrl is not the only way a value gets into DataStore: builds that
    // predate normalizeServerUrl wrote raw strings, and that is precisely the
    // population #149 is about. currentUrl feeds Intent.ACTION_VIEW ("Open web
    // app", which throws ActivityNotFoundException on a scheme-less URI) and
    // coverImageUrl (which returns null, blanking every cover). Guarding only the
    // Retrofit path left those broken.

    @Test
    fun `an unnormalized stored url is repaired on construction and written back`() = runBlocking {
        val dataStore = newDataStore()
        dataStore.edit { it[key] = "tandem.example.com" }

        val manager = ServerUrlManager(dataStore, "", seedScope)

        assertEquals("https://tandem.example.com", manager.currentUrl)
        assertEquals("https://tandem.example.com", dataStore.data.first()[key])
    }

    @Test
    fun `a stored url that cannot be repaired leaves the server unconfigured`() = runBlocking {
        val dataStore = newDataStore()
        dataStore.edit { it[key] = "not a url" }

        val manager = ServerUrlManager(dataStore, "", seedScope)

        // Blank, not the garbage: every consumer of currentUrl already handles
        // "unconfigured", and none of them handle "unparseable".
        assertEquals("", manager.currentUrl)
        assertNull(dataStore.data.first()[key])
    }

    @Test
    fun `an already-normalized stored url is not rewritten`() = runBlocking {
        val dataStore = newDataStore()
        dataStore.edit { it[key] = "https://my.own.server:8443" }

        val manager = ServerUrlManager(dataStore, "https://tandem.example.com", seedScope)

        assertEquals("https://my.own.server:8443", manager.currentUrl)
        assertEquals("https://my.own.server:8443", dataStore.data.first()[key])
    }

    @Test
    fun `a sub-path stored by an older build survives the repair`() = runBlocking {
        // The old retrofitBaseUrl kept the path, so these exist in the wild.
        val dataStore = newDataStore()
        dataStore.edit { it[key] = "https://host/tandem/" }

        val manager = ServerUrlManager(dataStore, "", seedScope)

        assertEquals("https://host/tandem", manager.currentUrl)
    }

    @Test
    fun `a scheme-less build default is normalised`() {
        // tandem.defaultServerUrl is a developer-set build property and gets the
        // same treatment as anything the user types.
        val manager = ServerUrlManager(newDataStore(), "tandem.example.com", seedScope)
        assertEquals("https://tandem.example.com", manager.currentUrl)
    }

    @Test
    fun `an unusable build default is treated as unconfigured`() {
        val manager = ServerUrlManager(newDataStore(), "not a url", seedScope)
        assertEquals("", manager.currentUrl)
    }

    @Test
    fun `the url flow reports normalized values`() = runBlocking {
        val dataStore = newDataStore()
        val manager = ServerUrlManager(dataStore, "tandem.example.com", seedScope)
        assertEquals("https://tandem.example.com", manager.serverUrlFlow.first())
    }
}
