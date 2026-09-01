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
import javax.inject.Singleton

private val KEY_ACCESS_TOKEN = stringPreferencesKey("access_token")
private val KEY_REFRESH_TOKEN = stringPreferencesKey("refresh_token")

@Singleton
class TokenManager @Inject constructor(
    private val dataStore: DataStore<Preferences>,
    @ApplicationScope scope: CoroutineScope
) {
    /**
     * The tokens, readable without suspending or touching disk.
     *
     * `AuthInterceptor` runs on an OkHttp dispatcher thread for every request and
     * once did a blocking DataStore read each time; issue #143 replaced that with a
     * cache seeded at construction. But Hilt builds this inside
     * `Application.onCreate`, so that seed was itself a disk read on the main
     * thread before the first frame — issue #318, which moved it off.
     *
     * A read arriving before the seed lands blocks rather than reporting "no
     * token": every request goes through here, and sending one unauthenticated is
     * a worse answer than waiting a moment for the right one.
     */
    private val seeded = SeededValue(scope) {
        val prefs = dataStore.data.first()
        prefs[KEY_ACCESS_TOKEN] to prefs[KEY_REFRESH_TOKEN]
    }

    /**
     * Set by [saveTokens]/[clearTokens] and preferred over the seed once it is.
     * A `null to null` pair is a real answer -- signed out -- and is why this is
     * a nullable Pair rather than two nullable fields.
     */
    @Volatile
    private var written: Pair<String?, String?>? = null

    fun cachedAccessToken(): String? = (written ?: seeded.get()).first

    fun currentRefreshToken(): String? = (written ?: seeded.get()).second
    fun getAccessToken(): Flow<String?> =
        dataStore.data.map { it[KEY_ACCESS_TOKEN] }

    fun getRefreshToken(): Flow<String?> =
        dataStore.data.map { it[KEY_REFRESH_TOKEN] }

    suspend fun saveTokens(accessToken: String, refreshToken: String) {
        written = accessToken to refreshToken
        dataStore.edit { prefs ->
            prefs[KEY_ACCESS_TOKEN] = accessToken
            prefs[KEY_REFRESH_TOKEN] = refreshToken
        }
    }

    suspend fun clearTokens() {
        written = null to null
        dataStore.edit { prefs ->
            prefs.remove(KEY_ACCESS_TOKEN)
            prefs.remove(KEY_REFRESH_TOKEN)
        }
    }

    /** The signed-in user, read from the stored token's subject. Null if unreadable. */
    suspend fun currentUserId(): Int? = userIdFromAccessToken(cachedAccessToken())
}
