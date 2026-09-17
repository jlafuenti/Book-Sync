package com.booksync.ui.tour

import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import javax.inject.Inject
import javax.inject.Singleton

/**
 * The two flags behind "offer once, replay any time" (issue #597 §3), stored
 * in the app's existing `booksync_prefs` DataStore (`di/AppModule.kt`) — no
 * new provider needed, `DataStore<Preferences>` is already injectable.
 */
@Singleton
class TourPrefs @Inject constructor(
    private val dataStore: DataStore<Preferences>,
) {
    private val offeredKey = booleanPreferencesKey("tour_offered")
    private val completedKey = booleanPreferencesKey("tour_completed")

    /** Whether the first-sign-in dialog has already been shown (and answered, either way). */
    val offered: Flow<Boolean> = dataStore.data.map { it[offeredKey] ?: false }

    /** Whether a run of the tour has ever reached the end, or been quit out of. */
    val completed: Flow<Boolean> = dataStore.data.map { it[completedKey] ?: false }

    suspend fun markOffered() {
        dataStore.edit { it[offeredKey] = true }
    }

    suspend fun markCompleted() {
        dataStore.edit { it[completedKey] = true }
    }
}
