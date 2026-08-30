package com.booksync.data.remote

import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.flow.map
import javax.inject.Inject
import javax.inject.Singleton

private val KEY_ACCESS_TOKEN = stringPreferencesKey("access_token")
private val KEY_REFRESH_TOKEN = stringPreferencesKey("refresh_token")

@Singleton
class TokenManager @Inject constructor(
    private val dataStore: DataStore<Preferences>
) {
    /**
     * The access token, readable without suspending or touching disk (issue #143).
     *
     * `AuthInterceptor` runs on an OkHttp dispatcher thread for every request and
     * used to do a blocking DataStore read each time. Seeded once at construction
     * and kept in step by [saveTokens]/[clearTokens]; `@Volatile` publishes writes
     * to the reader threads.
     */
    @Volatile
    private var cachedAccess: String? = null

    /** As above; only [TokenAuthenticator] needs it, and only on the refresh path. */
    @Volatile
    private var cachedRefresh: String? = null

    init {
        runBlocking {
            val prefs = dataStore.data.first()
            cachedAccess = prefs[KEY_ACCESS_TOKEN]
            cachedRefresh = prefs[KEY_REFRESH_TOKEN]
        }
    }

    fun cachedAccessToken(): String? = cachedAccess

    fun currentRefreshToken(): String? = cachedRefresh
    fun getAccessToken(): Flow<String?> =
        dataStore.data.map { it[KEY_ACCESS_TOKEN] }

    fun getRefreshToken(): Flow<String?> =
        dataStore.data.map { it[KEY_REFRESH_TOKEN] }

    suspend fun saveTokens(accessToken: String, refreshToken: String) {
        cachedAccess = accessToken
        cachedRefresh = refreshToken
        dataStore.edit { prefs ->
            prefs[KEY_ACCESS_TOKEN] = accessToken
            prefs[KEY_REFRESH_TOKEN] = refreshToken
        }
    }

    suspend fun clearTokens() {
        cachedAccess = null
        cachedRefresh = null
        dataStore.edit { prefs ->
            prefs.remove(KEY_ACCESS_TOKEN)
            prefs.remove(KEY_REFRESH_TOKEN)
        }
    }

    /** The signed-in user, read from the stored token's subject. Null if unreadable. */
    suspend fun currentUserId(): Int? = userIdFromAccessToken(cachedAccess)
}
