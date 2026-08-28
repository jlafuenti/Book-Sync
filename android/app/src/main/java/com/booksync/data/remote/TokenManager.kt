package com.booksync.data.remote

import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import javax.inject.Inject
import javax.inject.Singleton

private val KEY_ACCESS_TOKEN = stringPreferencesKey("access_token")
private val KEY_REFRESH_TOKEN = stringPreferencesKey("refresh_token")

/**
 * Which account the local Room cache currently belongs to (issue #314).
 * Persisted rather than derived on demand so a switch is detectable across
 * process death: the comparison needs the *previous* user, and the token has
 * already been replaced by the time we look.
 */
private val KEY_CACHED_USER_ID = intPreferencesKey("cached_user_id")

@Singleton
class TokenManager @Inject constructor(
    private val dataStore: DataStore<Preferences>
) {
    fun getAccessToken(): Flow<String?> =
        dataStore.data.map { it[KEY_ACCESS_TOKEN] }

    fun getRefreshToken(): Flow<String?> =
        dataStore.data.map { it[KEY_REFRESH_TOKEN] }

    suspend fun saveTokens(accessToken: String, refreshToken: String) {
        dataStore.edit { prefs ->
            prefs[KEY_ACCESS_TOKEN] = accessToken
            prefs[KEY_REFRESH_TOKEN] = refreshToken
        }
    }

    suspend fun clearTokens() {
        dataStore.edit { prefs ->
            prefs.remove(KEY_ACCESS_TOKEN)
            prefs.remove(KEY_REFRESH_TOKEN)
        }
        // KEY_CACHED_USER_ID deliberately survives a logout: it records who the
        // rows still in Room belong to, and those rows outlive the session. Losing
        // it would make the next sign-in look like a first-ever one and skip the
        // clear (issue #314).
    }

    /** The account the cached Room data belongs to, or null before the first sign-in. */
    suspend fun getCachedUserId(): Int? = dataStore.data.first()[KEY_CACHED_USER_ID]

    suspend fun setCachedUserId(userId: Int) {
        dataStore.edit { it[KEY_CACHED_USER_ID] = userId }
    }

    /** The signed-in user, read from the stored token's subject. Null if unreadable. */
    suspend fun currentUserId(): Int? = userIdFromAccessToken(getAccessToken().first())
}
