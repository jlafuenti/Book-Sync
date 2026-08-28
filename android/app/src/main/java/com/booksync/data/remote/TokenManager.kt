package com.booksync.data.remote

import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import javax.inject.Inject
import javax.inject.Singleton

private val KEY_ACCESS_TOKEN = stringPreferencesKey("access_token")
private val KEY_REFRESH_TOKEN = stringPreferencesKey("refresh_token")

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
    }

    /** The signed-in user, read from the stored token's subject. Null if unreadable. */
    suspend fun currentUserId(): Int? = userIdFromAccessToken(getAccessToken().first())
}
