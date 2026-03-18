package com.booksync.ui.settings

import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.UpdateMeRequest
import com.booksync.ui.theme.TandemTheme
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import javax.inject.Inject

val AUTO_CLEANUP_EBOOKS = booleanPreferencesKey("auto_cleanup_ebooks")
val AUTO_CLEANUP_AUDIOBOOKS = booleanPreferencesKey("auto_cleanup_audiobooks")
val APP_THEME = stringPreferencesKey("app_theme")

@HiltViewModel
class SettingsViewModel @Inject constructor(
    private val dataStore: DataStore<Preferences>,
    private val api: BookSyncApi
) : ViewModel() {

    val autoCleanupEbooks = dataStore.data.map { prefs ->
        prefs[AUTO_CLEANUP_EBOOKS] ?: false
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), false)

    val autoCleanupAudiobooks = dataStore.data.map { prefs ->
        prefs[AUTO_CLEANUP_AUDIOBOOKS] ?: false
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), false)

    val appTheme = dataStore.data.map { prefs ->
        TandemTheme.fromSlug(prefs[APP_THEME] ?: "blueprint")
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), TandemTheme.BLUEPRINT)

    fun setAutoCleanupEbooks(enabled: Boolean) {
        viewModelScope.launch {
            dataStore.edit { prefs ->
                prefs[AUTO_CLEANUP_EBOOKS] = enabled
            }
        }
    }

    fun setAutoCleanupAudiobooks(enabled: Boolean) {
        viewModelScope.launch {
            dataStore.edit { prefs ->
                prefs[AUTO_CLEANUP_AUDIOBOOKS] = enabled
            }
        }
    }

    fun setTheme(theme: TandemTheme) {
        viewModelScope.launch {
            // Save locally immediately
            dataStore.edit { prefs ->
                prefs[APP_THEME] = theme.slug
            }
            // Persist to server
            try {
                api.updateMe(UpdateMeRequest(theme = theme.slug))
            } catch (_: Exception) {
                // Local preference is already saved; server sync is best-effort
            }
        }
    }

    /** Sync server-saved theme into local DataStore (call after login). */
    fun syncThemeFromServer(slug: String) {
        viewModelScope.launch {
            dataStore.edit { prefs ->
                prefs[APP_THEME] = slug
            }
        }
    }
}
