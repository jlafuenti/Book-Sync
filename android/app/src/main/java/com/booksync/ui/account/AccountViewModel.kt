package com.booksync.ui.account

import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import android.content.Context
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.DEFAULT_SERVER_URL
import com.booksync.data.remote.PasswordChangeRequest
import com.booksync.data.remote.ServerUrlManager
import com.booksync.data.remote.TokenManager
import com.booksync.data.remote.UpdateMeRequest
import com.booksync.data.remote.UserResponse
import com.booksync.data.util.NetworkMonitor
import com.booksync.ui.theme.TandemTheme
import com.booksync.util.restartApp
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import retrofit2.HttpException
import javax.inject.Inject

// ---- DataStore preference keys (previously in ui/settings/SettingsViewModel.kt) ----

val AUTO_CLEANUP_EBOOKS     = booleanPreferencesKey("auto_cleanup_ebooks")
val AUTO_CLEANUP_AUDIOBOOKS = booleanPreferencesKey("auto_cleanup_audiobooks")
val APP_THEME               = stringPreferencesKey("app_theme")

/**
 * Represents the lifecycle of an in-flight password-change request.
 * Distinct states keep the UI simple — one `when` and one button-enabled check.
 */
sealed class ChangePasswordState {
    object Idle : ChangePasswordState()
    object Submitting : ChangePasswordState()
    object Success : ChangePasswordState()
    data class Error(val message: String) : ChangePasswordState()
}

/**
 * Account tab view-model. Merges the old SettingsViewModel concerns (theme, auto-cleanup)
 * with account-specific features (user profile, logout, password change).
 */
@HiltViewModel
class AccountViewModel @Inject constructor(
    private val dataStore: DataStore<Preferences>,
    private val api: BookSyncApi,
    private val tokenManager: TokenManager,
    private val serverUrlManager: ServerUrlManager,
    networkMonitor: NetworkMonitor,
) : ViewModel() {

    val serverUrl = serverUrlManager.serverUrlFlow
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000L), DEFAULT_SERVER_URL)

    fun saveServerUrlAndRestart(context: Context, url: String) {
        viewModelScope.launch {
            serverUrlManager.setServerUrl(url)
            restartApp(context)
        }
    }


    /** Live network reachability — used by the UI to gate "Change password" and show the offline chip. */
    val isOnline = networkMonitor.isOnline
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000L), networkMonitor.isOnline.value)

    // ---- Prefs-backed flows (reuse keys from SettingsViewModel) ------------

    val autoCleanupEbooks = dataStore.data.map { it[AUTO_CLEANUP_EBOOKS] ?: false }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000L), false)

    val autoCleanupAudiobooks = dataStore.data.map { it[AUTO_CLEANUP_AUDIOBOOKS] ?: false }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000L), false)

    val appTheme = dataStore.data.map { TandemTheme.fromSlug(it[APP_THEME] ?: "blueprint") }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000L), TandemTheme.BLUEPRINT)

    // ---- User profile (from GET /api/auth/me) ------------------------------

    private val _user = MutableStateFlow<UserResponse?>(null)
    val user = _user.asStateFlow()

    private val _userError = MutableStateFlow<String?>(null)
    val userError = _userError.asStateFlow()

    // ---- Change-password lifecycle -----------------------------------------

    private val _changePasswordState = MutableStateFlow<ChangePasswordState>(ChangePasswordState.Idle)
    val changePasswordState = _changePasswordState.asStateFlow()

    init {
        loadProfile()
    }

    /** Fetch the signed-in user from the server. Silent on network errors — cache keeps working. */
    fun loadProfile() {
        viewModelScope.launch {
            try {
                _user.value = api.getMe()
                _userError.value = null
            } catch (e: Exception) {
                _userError.value = e.message
            }
        }
    }

    // ---- Prefs setters -----------------------------------------------------

    fun setAutoCleanupEbooks(enabled: Boolean) {
        viewModelScope.launch { dataStore.edit { it[AUTO_CLEANUP_EBOOKS] = enabled } }
    }

    fun setAutoCleanupAudiobooks(enabled: Boolean) {
        viewModelScope.launch { dataStore.edit { it[AUTO_CLEANUP_AUDIOBOOKS] = enabled } }
    }

    fun setTheme(theme: TandemTheme) {
        viewModelScope.launch {
            dataStore.edit { it[APP_THEME] = theme.slug }
            // Best-effort server sync — the local preference is already live.
            runCatching { api.updateMe(UpdateMeRequest(theme = theme.slug)) }
        }
    }

    // ---- Password change ---------------------------------------------------

    /**
     * Submit a password change. Server validates `old_password`; on 400 we surface
     * "Current password is incorrect" verbatim.
     */
    fun changePassword(oldPassword: String, newPassword: String) {
        if (_changePasswordState.value is ChangePasswordState.Submitting) return
        _changePasswordState.value = ChangePasswordState.Submitting
        viewModelScope.launch {
            try {
                val response = api.changePassword(
                    PasswordChangeRequest(old_password = oldPassword, new_password = newPassword),
                )
                if (response.isSuccessful) {
                    _changePasswordState.value = ChangePasswordState.Success
                } else {
                    val msg = when (response.code()) {
                        400  -> "Current password is incorrect."
                        401  -> "Session expired. Please log in again."
                        else -> "Server error (HTTP ${response.code()})."
                    }
                    _changePasswordState.value = ChangePasswordState.Error(msg)
                }
            } catch (e: HttpException) {
                _changePasswordState.value = ChangePasswordState.Error("Server error: ${e.code()}")
            } catch (e: Exception) {
                _changePasswordState.value = ChangePasswordState.Error(e.message ?: "Network error")
            }
        }
    }

    fun resetChangePasswordState() { _changePasswordState.value = ChangePasswordState.Idle }

    // ---- Logout ------------------------------------------------------------

    /** Clears stored tokens; BookSyncNavigation observes the clear and routes to Login. */
    fun logout() {
        viewModelScope.launch {
            // Best-effort: invalidate server-side tokens, but always clear local
            // tokens even if the request fails (e.g. offline, token already expired).
            try {
                api.logout()
            } catch (_: Exception) {
                // ignore — local logout must still succeed
            }
            tokenManager.clearTokens()
        }
    }
}
