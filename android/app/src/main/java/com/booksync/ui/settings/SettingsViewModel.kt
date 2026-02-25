package com.booksync.ui.settings

import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import javax.inject.Inject

val AUTO_CLEANUP_EBOOKS = booleanPreferencesKey("auto_cleanup_ebooks")
val AUTO_CLEANUP_AUDIOBOOKS = booleanPreferencesKey("auto_cleanup_audiobooks")

@HiltViewModel
class SettingsViewModel @Inject constructor(
    private val dataStore: DataStore<Preferences>
) : ViewModel() {

    val autoCleanupEbooks = dataStore.data.map { prefs ->
        prefs[AUTO_CLEANUP_EBOOKS] ?: false
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), false)

    val autoCleanupAudiobooks = dataStore.data.map { prefs ->
        prefs[AUTO_CLEANUP_AUDIOBOOKS] ?: false
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), false)

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
}
