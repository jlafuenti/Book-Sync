package com.booksync.data.remote

import android.os.Build
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.runBlocking
import java.util.UUID
import javax.inject.Inject
import javax.inject.Singleton

private val KEY_DEVICE_ID = stringPreferencesKey("device_id")
private val KEY_DEVICE_NAME_OVERRIDE = stringPreferencesKey("device_name_override")

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

    /** Auto-derived fallback name, e.g. "Google Pixel 9 Pro XL". Computed once; stable for the process lifetime. */
    private val autoDeviceName: String = "${Build.MANUFACTURER} ${Build.MODEL}"

    /**
     * Cached, user-facing device name: the persisted override if the user has set one
     * via [setDeviceName], otherwise [autoDeviceName]. Synchronously readable like
     * [deviceId] — existing callers (e.g. `BookSyncRepository`) need no changes.
     * See [deviceNameFlow] for a reactive stream (mirrors `ServerUrlManager.serverUrlFlow`).
     */
    @Volatile
    var deviceName: String = autoDeviceName
        private set

    /** Reactive device-name stream for UI, mirroring [ServerUrlManager.serverUrlFlow]. */
    val deviceNameFlow: Flow<String> =
        dataStore.data.map { it[KEY_DEVICE_NAME_OVERRIDE] ?: autoDeviceName }

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
        deviceName = runBlocking {
            dataStore.data.first()[KEY_DEVICE_NAME_OVERRIDE] ?: autoDeviceName
        }
    }

    /**
     * Sets a user-chosen device name override. Passing `null`, or a blank/whitespace-only
     * string, clears the override and reverts to the auto-derived name.
     */
    suspend fun setDeviceName(name: String?) {
        val trimmed = name?.trim()
        if (trimmed.isNullOrBlank()) {
            deviceName = autoDeviceName
            dataStore.edit { it.remove(KEY_DEVICE_NAME_OVERRIDE) }
        } else {
            deviceName = trimmed
            dataStore.edit { it[KEY_DEVICE_NAME_OVERRIDE] = trimmed }
        }
    }
}
