package com.booksync.data.remote

import androidx.datastore.core.DataStore
import com.booksync.di.ApplicationScope
import kotlinx.coroutines.CoroutineScope
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import javax.inject.Inject
import javax.inject.Named
import javax.inject.Singleton

private val KEY_SERVER_URL = stringPreferencesKey("server_url")

@Singleton
class ServerUrlManager @Inject constructor(
    private val dataStore: DataStore<Preferences>,
    /**
     * The URL a fresh install starts on, from the `tandem.defaultServerUrl` build
     * property. Empty in a clean clone — the login screen then asks for one.
     * Injected rather than read from [com.booksync.BuildConfig] here so the
     * unconfigured case is testable.
     */
    @param:Named(DEFAULT_SERVER_URL_QUALIFIER) private val defaultUrl: String,
    // Last, so the existing positional call sites in the tests keep working.
    @ApplicationScope scope: CoroutineScope,
) {
    /**
     * Cached server URL. Initialized once at construction from DataStore and only mutated by
     * [setServerUrl]. Safe to read synchronously from any thread (including the network thread)
     * since `@Volatile` guarantees visibility of writes across threads.
     *
     * Note: changing the URL requires an app restart to fully take effect (Retrofit's baseUrl
     * is set at singleton creation), but this property is kept in sync so callers that build
     * URLs at use-time (cover images, audio streams) immediately see the new value.
     */
    val currentUrl: String get() = chosenUrl ?: seeded.get()

    /**
     * Seeded off the main thread (issue #318); this loader also *persists* a
     * repaired value, so it must run exactly once -- see [SeededValue].
     */
    private val seeded = SeededValue(scope) {
        val stored = dataStore.data.first()[KEY_SERVER_URL]
        when (stored) {
            null -> repair(defaultUrl)
            else -> repair(stored).also { if (it != stored) persist(it) }
        }
    }

    /** Set by [setServerUrl]; wins over the seed once the user picks a server. */
    @Volatile
    private var chosenUrl: String? = null

    /**
     * Normalize a value read from storage, or blank it out if it can't be.
     *
     * Issue #149 guarded the *write* path, but `setServerUrl` is not the only way a
     * value gets into DataStore: every build that predates the validation wrote raw
     * strings, and that is exactly the population the issue is about. [currentUrl]
     * is read directly by `Intent.ACTION_VIEW` ("Open web app" on the Account and
     * Home screens, which throws `ActivityNotFoundException` on a scheme-less URI)
     * and by `coverImageUrl` (which returns null, blanking every cover in the app).
     * Guarding only Retrofit would have left an upgrading user with a launchable app
     * that had no covers and a button that crashed it.
     *
     * Blank rather than the original garbage, because every consumer already handles
     * "no server configured" and none of them handle "unparseable".
     */
    private fun repair(value: String): String =
        if (value.isBlank()) "" else normalizeServerUrl(value) ?: ""

    private suspend fun persist(value: String) {
        dataStore.edit { prefs ->
            if (value.isEmpty()) prefs.remove(KEY_SERVER_URL) else prefs[KEY_SERVER_URL] = value
        }
    }

    /** Convenience for the Retrofit setup in AppModule. */
    fun getServerUrlBlocking(): String = currentUrl

    /** Normalized, for the same reason [repair] exists — the UI displays this. */
    val serverUrlFlow: Flow<String> =
        dataStore.data.map { repair(it[KEY_SERVER_URL] ?: defaultUrl) }

    /**
     * Persist a new server URL, or refuse it.
     *
     * Returns false and changes nothing when [normalizeServerUrl] can't turn the
     * input into an http(s) origin. Callers must not restart the app on false —
     * that is exactly how issue #149 bricked installs: the raw string was stored,
     * the process was killed, and Retrofit then threw inside Hilt on every launch
     * with no UI left to correct it from.
     */
    suspend fun setServerUrl(url: String): Boolean {
        val normalized = normalizeServerUrl(url) ?: return false
        chosenUrl = normalized
        persist(normalized)
        return true
    }
}
