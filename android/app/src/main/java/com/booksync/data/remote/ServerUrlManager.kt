package com.booksync.data.remote

import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.runBlocking
import javax.inject.Inject
import javax.inject.Singleton

private val KEY_SERVER_URL = stringPreferencesKey("server_url")

const val DEFAULT_SERVER_URL = "https://tandem.example.com"

/** Old production hostname; its DNS record no longer exists. Stored values are migrated. */
const val LEGACY_SERVER_URL = "https://booksync.example.com"

@Singleton
class ServerUrlManager @Inject constructor(
    private val dataStore: DataStore<Preferences>
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
    @Volatile
    var currentUrl: String = DEFAULT_SERVER_URL
        private set

    init {
        currentUrl = runBlocking {
            val stored = dataStore.data.first()[KEY_SERVER_URL]
            if (stored == LEGACY_SERVER_URL) {
                dataStore.edit { it[KEY_SERVER_URL] = DEFAULT_SERVER_URL }
                DEFAULT_SERVER_URL
            } else {
                stored ?: DEFAULT_SERVER_URL
            }
        }
    }

    /** Convenience for the Retrofit setup in AppModule. */
    fun getServerUrlBlocking(): String = currentUrl

    val serverUrlFlow: Flow<String> =
        dataStore.data.map { it[KEY_SERVER_URL] ?: DEFAULT_SERVER_URL }

    suspend fun setServerUrl(url: String) {
        val normalized = url.trimEnd('/')
        currentUrl = normalized
        dataStore.edit { it[KEY_SERVER_URL] = normalized }
    }
}
