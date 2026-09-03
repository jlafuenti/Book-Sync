package com.booksync.data.remote

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Whether the first-run welcome screen is still showing (issue #175).
 *
 * Application-scoped, like [PasswordResetGate], and for the same kind of reason:
 * the thing that changes this state also destroys whatever was holding it.
 *
 * Concretely — "Check connection" stores the server URL when the probe succeeds,
 * and storing it re-creates the login destination. A `rememberSaveable` in the
 * composable dies with that destination, and so does a flag on a ViewModel
 * scoped to it: the freshly built ViewModel would look at a now-configured URL,
 * conclude this is not a first run, and drop the user on a password prompt —
 * which, when it happened after a *failed* probe, was the reported bug. Only
 * state that outlives the destination can answer "is the user still on the
 * welcome screen".
 *
 * Two facts, and the distinction matters:
 *  - [startedUnconfigured] — latched from the URL the app launched with, once
 *    per process. Recomputing it later gives the wrong answer for exactly the
 *    reason above.
 *  - [dismissed] — set when the user taps "Continue to sign in" or skips.
 *
 * Process-lifetime, not persisted: a relaunch with a working server is not a
 * first run, and a relaunch still without one should see the screen again.
 */
@Singleton
class FirstRunGate @Inject constructor() {

    private val _dismissed = MutableStateFlow(false)
    val dismissed: StateFlow<Boolean> = _dismissed.asStateFlow()

    @Volatile
    private var startedUnconfigured: Boolean? = null

    /**
     * Whether this process launched without a server, deciding it from
     * [serverUrl] the first time it is asked and remembering that answer.
     *
     * Not read from [ServerUrlManager] here: `currentUrl` blocks on the DataStore
     * seed, and this is a `@Singleton` Hilt builds during `Application.onCreate`
     * — the main-thread disk read issue #318 took out.
     */
    @Synchronized
    fun startedUnconfigured(serverUrl: String): Boolean =
        startedUnconfigured ?: shouldShowFirstRun(serverUrl).also { startedUnconfigured = it }

    /** The user is done with the welcome screen; don't show it again this run. */
    fun dismiss() {
        _dismissed.value = true
    }
}
