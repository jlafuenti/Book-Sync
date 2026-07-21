package com.booksync.data.remote

import android.os.Build
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import java.util.UUID
import javax.inject.Inject
import javax.inject.Singleton

private val KEY_DEVICE_ID = stringPreferencesKey("device_id")

/**
 * Provides a stable per-install device identity used to attribute bookmark/progress
 * writes to a specific device (see issue #54's multi-device conflict resolution
 * contract: writes carry `device_id` + `device_name`, and the server rejects stale
 * writes with HTTP 409).
 */
@Singleton
class DeviceIdManager @Inject constructor(
    private val dataStore: DataStore<Preferences>
) {
    /**
     * Cached device ID. Initialized once at construction from DataStore, generating
     * and persisting a new UUID on first run. Safe to read synchronously from any
     * thread since `@Volatile` guarantees visibility of writes across threads.
     */
    @Volatile
    var deviceId: String = ""
        private set

    /** Friendly device name, e.g. "Pixel 7". Not persisted or overridable (yet). */
    val deviceName: String = "${Build.MANUFACTURER} ${Build.MODEL}"

    init {
        deviceId = runBlocking {
            val stored = dataStore.data.first()[KEY_DEVICE_ID]
            if (stored != null) {
                stored
            } else {
                val generated = UUID.randomUUID().toString()
                dataStore.edit { it[KEY_DEVICE_ID] = generated }
                generated
            }
        }
    }
}
