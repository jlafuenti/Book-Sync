package com.booksync.data.remote

import android.os.Build
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import com.booksync.di.ApplicationScope
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
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
    private val dataStore: DataStore<Preferences>,
    @ApplicationScope scope: CoroutineScope,
) {
    /** Auto-derived fallback name, e.g. "Google Pixel 9 Pro XL". Computed once; stable for the process lifetime. */
    private val autoDeviceName: String = "${Build.MANUFACTURER} ${Build.MODEL}"

    /**
     * The device id, seeded off the main thread (issue #318).
     *
     * This loader is the reason [SeededValue] memoises rather than allowing a
     * blocking fallback: on a first launch it *generates and persists* a UUID.
     * Run it twice concurrently and the app mints two, persists one and caches the
     * other. This id keys `position_hints` and decides which device a reading
     * position came from, so a wrong one misattributes reading history — and it
     * would only misfire on a first launch, which makes it near-undiagnosable
     * afterwards.
     */
    private val seededDeviceId = SeededValue(scope) {
        dataStore.data.first()[KEY_DEVICE_ID]
            ?: UUID.randomUUID().toString().also { generated ->
                dataStore.edit { it[KEY_DEVICE_ID] = generated }
            }
    }

    private val seededDeviceName = SeededValue(scope) {
        dataStore.data.first()[KEY_DEVICE_NAME_OVERRIDE] ?: autoDeviceName
    }

    /** Set by [setDeviceName]; wins over the seeded value once the user chooses one. */
    @Volatile
    private var overrideName: String? = null

    /**
     * Cached device ID, generated and persisted on first run.
     *
     * Still a synchronous read for callers. It blocks only if something asks
     * before the seed has landed, which is the correct trade: attributing a write
     * to an empty device id would be worse than waiting for the real one.
     */
    val deviceId: String get() = seededDeviceId.get()

    /**
     * Cached, user-facing device name: the persisted override if the user has set one
     * via [setDeviceName], otherwise [autoDeviceName]. Synchronously readable like
     * [deviceId] — existing callers (e.g. `BookSyncRepository`) need no changes.
     * See [deviceNameFlow] for a reactive stream (mirrors `ServerUrlManager.serverUrlFlow`).
     */
    val deviceName: String get() = overrideName ?: seededDeviceName.get()

    /** Reactive device-name stream for UI, mirroring [ServerUrlManager.serverUrlFlow]. */
    val deviceNameFlow: Flow<String> =
        dataStore.data.map { it[KEY_DEVICE_NAME_OVERRIDE] ?: autoDeviceName }

    /**
     * Sets a user-chosen device name override. Passing `null`, or a blank/whitespace-only
     * string, clears the override and reverts to the auto-derived name.
     */
    suspend fun setDeviceName(name: String?) {
        val trimmed = name?.trim()
        if (trimmed.isNullOrBlank()) {
            overrideName = autoDeviceName
            dataStore.edit { it.remove(KEY_DEVICE_NAME_OVERRIDE) }
        } else {
            overrideName = trimmed
            dataStore.edit { it[KEY_DEVICE_NAME_OVERRIDE] = trimmed }
        }
    }
}
