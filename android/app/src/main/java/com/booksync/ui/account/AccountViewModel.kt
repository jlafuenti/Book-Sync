package com.booksync.ui.account

import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import android.content.Context
import com.booksync.data.remote.AccountDeleteRequest
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.INVALID_SERVER_URL_MESSAGE
import com.booksync.data.remote.DeviceIdManager
import com.booksync.data.remote.PasswordResetGate
import com.booksync.data.remote.PasswordChangeRequest
import com.booksync.data.remote.ServerUrlManager
import com.booksync.data.remote.TokenManager
import com.booksync.data.remote.serverDetail
import com.booksync.data.remote.UpdateMeRequest
import com.booksync.data.remote.UserResponse
import com.booksync.data.util.NetworkMonitor
import com.booksync.ui.theme.TandemTheme
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
 * Lifecycle of an in-flight account deletion (issue #146). Same shape as
 * [ChangePasswordState] — one `when` in the dialog, one button-enabled check.
 */
sealed class DeleteAccountState {
    object Idle : DeleteAccountState()
    object Submitting : DeleteAccountState()
    object Success : DeleteAccountState()
    data class Error(val message: String) : DeleteAccountState()
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
    private val deviceIdManager: DeviceIdManager,
    private val passwordResetGate: PasswordResetGate,
    networkMonitor: NetworkMonitor,
) : ViewModel() {

    val serverUrl = serverUrlManager.serverUrlFlow
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000L), serverUrlManager.currentUrl)

    /** Non-null while the last save was refused; see [saveServerUrl]. */
    private val _serverUrlError = MutableStateFlow<String?>(null)
    val serverUrlError = _serverUrlError.asStateFlow()

    fun clearServerUrlError() {
        _serverUrlError.value = null
    }

    /**
     * Point the app at a different server (issue #228).
     *
     * No longer restarts: [com.booksync.data.remote.BaseUrlInterceptor] reads the
     * URL per request, so the change takes effect on the next call. The old
     * `Runtime.getRuntime().exit(0)` could cut off the application-scoped,
     * non-cancellable position flush the sync contract depends on.
     *
     * Clearing the tokens is not tidiness — without it the previous server's
     * bearer is sent to the new host, and on its 401 the refresh token follows.
     * BookSyncNavigation observes the clear and routes to login.
     *
     * The cached library stays: it is keyed by server *and* user (issue #314), so
     * it cannot be misread as the new server's data, and switching back restores
     * it without a re-download.
     */
    fun saveServerUrl(url: String) {
        viewModelScope.launch {
            // Issue #149: only act if the URL was actually accepted. Acting on a
            // value Retrofit can't parse is what made the app un-launchable.
            if (serverUrlManager.setServerUrl(url)) {
                tokenManager.clearTokens()
            } else {
                _serverUrlError.value = INVALID_SERVER_URL_MESSAGE
            }
        }
    }

    val deviceName = deviceIdManager.deviceNameFlow
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000L), deviceIdManager.deviceName)

    fun setDeviceName(name: String) {
        viewModelScope.launch { deviceIdManager.setDeviceName(name) }
    }

    fun resetDeviceName() {
        viewModelScope.launch { deviceIdManager.setDeviceName(null) }
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
                val me = api.getMe()
                _user.value = me
                _userError.value = null
                // Second trigger for the forced-reset gate (issue #209). The 403
                // interceptor is the primary one, but /api/auth/me is on the
                // server's allow-list and so answers 200 for a flagged user —
                // meaning this is the one authenticated call that reports the flag
                // instead of being refused. Without it the gate has a single
                // trigger, and anything that drops that one signal strands the user
                // in an app where every other screen fails.
                if (me.must_reset_password) passwordResetGate.raise()
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
                    // The server bumped token_version, so the tokens still held
                    // here are already dead (issue #143). Leaving them in place
                    // meant the next request 401'd, the refresh 401'd too, and the
                    // app deadlocked. Ending the session locally is honest about
                    // what the server just did; BookSyncNavigation observes the
                    // clear and routes to login.
                    tokenManager.clearTokens()
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

    // ---- Account deletion --------------------------------------------------

    private val _deleteAccountState = MutableStateFlow<DeleteAccountState>(DeleteAccountState.Idle)
    val deleteAccountState = _deleteAccountState.asStateFlow()

    /**
     * Delete this account on the server, then end the session locally (#146).
     *
     * Play requires an in-app deletion path for any app that offers account
     * creation, and the login screen offers one. The server takes the password
     * in the body and answers 204; the tokens held here then describe an account
     * that no longer exists, so they are cleared exactly as [changePassword]
     * does — leaving them is the #143 deadlock, where the next request 401s and
     * the refresh 401s too. BookSyncNavigation observes the clear and routes to
     * Login.
     *
     * A refusal keeps the session and shows the server's own sentence: 403 and
     * 409 here mean "wrong password" and "you are the last active superadmin",
     * two different problems with two different fixes, and the status code
     * describes neither (issue #221).
     */
    fun deleteAccount(password: String) {
        if (_deleteAccountState.value is DeleteAccountState.Submitting) return
        _deleteAccountState.value = DeleteAccountState.Submitting
        viewModelScope.launch {
            try {
                val response = api.deleteAccount(AccountDeleteRequest(password = password))
                if (response.isSuccessful) {
                    _deleteAccountState.value = DeleteAccountState.Success
                    tokenManager.clearTokens()
                } else {
                    _deleteAccountState.value = DeleteAccountState.Error(
                        HttpException(response).serverDetail()
                            ?: "Couldn't delete the account (HTTP ${response.code()}).",
                    )
                }
            } catch (e: HttpException) {
                _deleteAccountState.value = DeleteAccountState.Error(
                    e.serverDetail() ?: "Couldn't delete the account (HTTP ${e.code()}).",
                )
            } catch (e: Exception) {
                _deleteAccountState.value = DeleteAccountState.Error(e.message ?: "Network error")
            }
        }
    }

    fun resetDeleteAccountState() { _deleteAccountState.value = DeleteAccountState.Idle }

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
