package com.booksync.ui.account

import com.booksync.diagnostics.buildProblemReport
import com.booksync.diagnostics.LogChannel
import com.booksync.diagnostics.DiagnosticLogger
import com.booksync.diagnostics.ReportProblemPlan
import com.booksync.diagnostics.planReportProblemIntent
import com.booksync.deviceCrashContext
import com.booksync.R
import androidx.core.content.FileProvider
import android.content.Intent
import android.net.Uri
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import android.content.Context
import com.booksync.data.local.dao.SyncPointDao
import com.booksync.data.local.dao.SyncMapStorageStats
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
import com.booksync.data.repository.BookSyncRepository
import com.booksync.data.repository.SyncMapRemovalStore
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
import java.io.File
import javax.inject.Inject

// ---- DataStore preference keys (previously in ui/settings/SettingsViewModel.kt) ----

val AUTO_CLEANUP_EBOOKS     = booleanPreferencesKey("auto_cleanup_ebooks")
val AUTO_CLEANUP_AUDIOBOOKS = booleanPreferencesKey("auto_cleanup_audiobooks")
val APP_THEME               = stringPreferencesKey("app_theme")

// Issue #655: per-device sync-map prefetch controls. Bandwidth and storage are
// properties of this phone, not the account, so these live here — in the local
// DataStore, next to auto-cleanup — rather than in the server's system_settings,
// which would wrongly apply one device's Wi-Fi preference to every device on the
// account. Both default on: "with the ebook" because the user already accepted a
// download at that moment and the map is the smaller half of what they asked
// for; "Wi-Fi only" because a silent multi-megabyte cellular pull is a cost the
// user did not ask for. `DownloadWorker` reads both directly from this DataStore.
val SYNC_MAP_WITH_EBOOK = booleanPreferencesKey("sync_map_with_ebook")
val SYNC_MAP_WIFI_ONLY  = booleanPreferencesKey("sync_map_wifi_only")

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
    private val diagnosticLogger: DiagnosticLogger,
    @param:dagger.hilt.android.qualifiers.ApplicationContext private val appContext: Context,
    private val repository: BookSyncRepository,
    private val syncPointDao: SyncPointDao,
    private val syncMapRemovalStore: SyncMapRemovalStore,
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

    /** "Download sync maps with the ebook" (issue #655) — default on. */
    val syncMapWithEbook = dataStore.data.map { it[SYNC_MAP_WITH_EBOOK] ?: true }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000L), true)

    /** "Only download sync maps over Wi-Fi" (issue #655) — default on. */
    val syncMapWifiOnly = dataStore.data.map { it[SYNC_MAP_WIFI_ONLY] ?: true }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000L), true)

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

    // ---- Sync data storage (issue #678) ------------------------------------
    //
    // Declared before `init` below on purpose: Kotlin runs property
    // initializers and init blocks in textual order, and `init` calls
    // `refreshSyncMapStorage()`, which reads `_syncMapStorage` — declaring it
    // afterwards left the field null the first time `init` ran (caught by
    // AccountViewModelSyncMapStorageTest, not by inspection).

    /** Backs the Account → Storage "Sync data — N books, about X MB" line. */
    private val _syncMapStorage = MutableStateFlow(SyncMapStorageStats(pairCount = 0, pointCount = 0, textPreviewBytes = 0))
    val syncMapStorage = _syncMapStorage.asStateFlow()

    init {
        loadProfile()
        refreshSyncMapStorage()
    }

    /** Re-reads the row/byte counts. Called at init and after [clearAllSyncMaps]. */
    fun refreshSyncMapStorage() {
        viewModelScope.launch {
            _syncMapStorage.value = syncPointDao.storageStats()
        }
    }

    /**
     * "Clear" in Account → Storage: drops every cached sync map on the device
     * and marks every one of those pairs "removed by you" (issue #678) — the
     * per-book "Remove sync data" action, applied to the whole cache at once
     * — so the #537 sweep does not immediately re-fetch what was just
     * cleared. A fresh download or "Refresh sync data" for a given pair
     * un-marks it as usual.
     */
    fun clearAllSyncMaps() {
        viewModelScope.launch {
            val pairIds = syncPointDao.distinctPairIds()
            pairIds.forEach { repository.clearSyncMapCache(it) }
            syncMapRemovalStore.markRemoved(pairIds.toSet())
            refreshSyncMapStorage()
        }
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

    fun setSyncMapWithEbook(enabled: Boolean) {
        viewModelScope.launch { dataStore.edit { it[SYNC_MAP_WITH_EBOOK] = enabled } }
    }

    fun setSyncMapWifiOnly(enabled: Boolean) {
        viewModelScope.launch { dataStore.edit { it[SYNC_MAP_WIFI_ONLY] = enabled } }
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

    /**
     * Sign out on every device (issue #250) — `POST /api/auth/logout-all`.
     *
     * [logout] ends this device's session only, which is right for putting the
     * phone down and no use at all when the phone is the thing that was lost.
     * This is the account-wide revoke, and it is the one normal user action that
     * still bumps `token_version`: every token the account holds dies, on every
     * device, immediately.
     *
     * Same shape as [logout] deliberately, and the same reasons: revoke first so
     * AuthInterceptor still has a bearer token to send, and clear locally no
     * matter what came back. A device that stays "signed in" because the request
     * failed is the exact failure this button exists to prevent.
     */
    fun logoutAll() {
        viewModelScope.launch {
            try {
                api.logoutAll()
            } catch (_: Exception) {
                // ignore — the local half must still happen
            }
            tokenManager.clearTokens()
        }
    }

    // ---- Report a problem --------------------------------------------------

    /**
     * Bundle the app diagnostics log, the app version and the device into a
     * "Report a problem" intent (issue #230).
     *
     * This is the whole crash-reporting story before launch: no SDK, no
     * third-party processor, and Play Vitals only sees users who share usage
     * data. The log already contains any crash the uncaught-exception handler
     * caught ([com.booksync.diagnostics.CrashLogHandler]), so this reaches the
     * traces even from someone who never turned diagnostics on.
     *
     * Issue #609: this used to always build a generic `ACTION_SEND` share,
     * which offered the whole share sheet — messaging apps, notes, the
     * clipboard — for a report that only ever goes one useful place. It now
     * opens straight to a mail app addressed at [com.booksync.diagnostics.SUPPORT_EMAIL]
     * when one resolves, and only falls back to the old share sheet when none
     * does, so the button is never dead on a device with no mail app.
     *
     * The version and device are repeated in the message body, not only in the
     * attached logs: some share targets drop attachments without saying so, and
     * a text-only report should still be triageable. [buildProblemReport] builds
     * that body and is unit-tested; [planReportProblemIntent] decides the rest
     * (destination, subject, which logs to attach) and is unit-tested too —
     * everything below that point (PackageManager, FileProvider, Intent extras)
     * is Android plumbing with nothing to assert on the JVM.
     *
     * Issue #636: both diagnostic channels are read, and only the ones with
     * content are ever named as attached or handed to the `Intent` — a missing
     * Auto log (nobody turned it on, or nobody has driven since) is the normal
     * case, not an error.
     */
    fun shareProblemReport(onIntent: (Intent) -> Unit) {
        val logTextByChannel = LogChannel.entries.associateWith { channel ->
            val file = diagnosticLogger.getLogFile(channel)
            if (file.exists()) runCatching { file.readText() }.getOrDefault("") else ""
        }
        val report = buildProblemReport(deviceCrashContext(), logTextByChannel)
        val plan = planReportProblemIntent(report, mailAppAvailable = resolvesMailApp(appContext))
        val logFiles = plan.attachments.map { diagnosticLogger.getLogFile(it) }

        onIntent(buildReportProblemIntent(appContext, plan, logFiles))
    }
}

/**
 * True when at least one installed app resolves a `mailto:` `ACTION_SENDTO`
 * intent — the probe [AccountViewModel.shareProblemReport] uses to decide
 * whether "Report a problem" can go straight to an email app instead of
 * falling back to the share sheet (issue #609).
 *
 * Needs the `<queries>` SENDTO/mailto entry in `AndroidManifest.xml`: Android
 * 11+ package visibility hides other apps' mail activities from
 * `resolveActivity` without it, so the probe would always come back empty and
 * the fallback would fire on every device, mail app or not.
 */
private fun resolvesMailApp(context: Context): Boolean {
    val probe = Intent(Intent.ACTION_SENDTO, Uri.parse("mailto:"))
    return probe.resolveActivity(context.packageManager) != null
}

/**
 * Turn a [ReportProblemPlan] into the real `Intent` (issue #609, extended for
 * two log files by #636).
 *
 * Straight to a mail app: `ACTION_SEND`/`ACTION_SEND_MULTIPLE` with
 * `type = "message/rfc822"` and `EXTRA_EMAIL`. The issue's implementation
 * notes flag `ACTION_SENDTO` + `mailto:` as the more reliable "email apps
 * only" filter, but that form does not carry `EXTRA_STREAM` — the
 * `message/rfc822` MIME type is what email clients register for specifically
 * (unlike the generic `text/plain` the old share used), so it keeps the same
 * "email apps only" targeting while still letting the diagnostics logs ride
 * along as attachments.
 *
 * No mail app: today's generic chooser, unchanged — a live but unrouted share
 * beats a dead button.
 *
 * [Intent.ACTION_SEND] takes at most one [Intent.EXTRA_STREAM] `Uri`;
 * [Intent.ACTION_SEND_MULTIPLE] takes an `ArrayList<Uri>` under the same
 * extra key instead. [logFiles] (already filtered to [ReportProblemPlan.attachments]
 * — only logs that exist) picks between them by count, so a report with one
 * attachment keeps the exact shape #609 pinned and a report with two — app and
 * Auto both present — becomes `ACTION_SEND_MULTIPLE`. `FLAG_GRANT_READ_URI_PERMISSION`
 * grants every `content://` URI in `EXTRA_STREAM`, list or single, to whichever
 * app ends up handling the intent, chooser or not — that grant is what makes
 * the attachment visible to the target that resolves it.
 */
private fun buildReportProblemIntent(context: Context, plan: ReportProblemPlan, logFiles: List<File>): Intent {
    val uris = logFiles.map { file ->
        FileProvider.getUriForFile(context, "${context.packageName}.fileprovider", file)
    }
    val send = Intent(if (uris.size > 1) Intent.ACTION_SEND_MULTIPLE else Intent.ACTION_SEND).apply {
        type = if (plan.toMailApp) "message/rfc822" else "text/plain"
        if (plan.toMailApp) putExtra(Intent.EXTRA_EMAIL, arrayOf(plan.recipient))
        putExtra(Intent.EXTRA_SUBJECT, plan.subject)
        putExtra(Intent.EXTRA_TEXT, plan.body)
        if (uris.isNotEmpty()) {
            if (uris.size > 1) {
                putParcelableArrayListExtra(Intent.EXTRA_STREAM, ArrayList(uris))
            } else {
                putExtra(Intent.EXTRA_STREAM, uris.single())
            }
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
    }
    return if (plan.toMailApp) {
        send
    } else {
        Intent.createChooser(send, context.getString(R.string.report_problem_chooser))
    }
}
