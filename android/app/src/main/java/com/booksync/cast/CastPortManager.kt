package com.booksync.cast

import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import com.booksync.data.remote.SeededValue
import com.booksync.di.ApplicationScope
import com.booksync.player.DEFAULT_CAST_PORT
import com.booksync.player.isValidCastPort
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map

private val KEY_CAST_PORT = intPreferencesKey("cast_port")

/**
 * The port the phone's LAN cast server listens on, as the user set it on the
 * Account screen (issue #931). Stored in DataStore like the server URL and
 * applied at the next Cast session: `LocalCastServerController` reads
 * [currentPort] at every start.
 *
 * A value outside 1024..65535 is never stored, and one that somehow is (an
 * older build, a hand-edited file) reads back as the default, so the server
 * never tries to bind a port the Account field would have refused.
 */
@Singleton
class CastPortManager @Inject constructor(
    private val dataStore: DataStore<Preferences>,
    @ApplicationScope scope: CoroutineScope,
) {
    /** Seeded off the main thread, like [com.booksync.data.remote.ServerUrlManager.currentUrl]. */
    private val seeded = SeededValue(scope) { repair(dataStore.data.first()[KEY_CAST_PORT]) }

    /** Set by [setPort]; wins over the seed once the user picks a port. */
    @Volatile
    private var chosen: Int? = null

    /** Safe to read from any thread; blocks only until the first DataStore read lands. */
    val currentPort: Int get() = chosen ?: seeded.get()

    /** For the Account field. */
    val port: Flow<Int> = dataStore.data.map { repair(it[KEY_CAST_PORT]) }

    /**
     * Persist [port], or refuse it. Returns false and changes nothing when it is
     * outside 1024..65535; the caller shows the current value again.
     */
    suspend fun setPort(port: Int): Boolean {
        if (!isValidCastPort(port)) return false
        chosen = port
        dataStore.edit { it[KEY_CAST_PORT] = port }
        return true
    }

    private fun repair(stored: Int?): Int =
        if (stored != null && isValidCastPort(stored)) stored else DEFAULT_CAST_PORT
}
