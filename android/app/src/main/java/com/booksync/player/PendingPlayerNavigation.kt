package com.booksync.player

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

/**
 * Carries a route from a tap on the playback notification (issue #684) —
 * read by `MainActivity` in `onCreate`/`onNewIntent`, which can run before
 * `BookSyncNavigation` has composed at all — over to the navigation graph
 * that actually owns the `NavController`.
 *
 * A [StateFlow] rather than a one-shot event channel: on a cold start, the
 * Activity sets this before `BookSyncNavigation`'s collector even exists,
 * and a `MutableSharedFlow` with no replay would drop an emission nobody
 * was listening for yet. [consume] hands the pending route to whoever asks
 * first and clears it, so a recomposition or a second reader can't re-apply
 * the same navigation twice.
 *
 * [consume] also drops the route outright when [signedIn] is false, rather
 * than leaving it queued for whenever the user next signs in — a tap that
 * arrived while signed out (or mid-onboarding) should open the app
 * normally, not surprise-navigate the moment login later succeeds.
 */
object PendingPlayerNavigation {
    private val _route = MutableStateFlow<String?>(null)
    val route: StateFlow<String?> = _route

    /** Records [route] as pending. Overwrites one that was never consumed. */
    fun set(route: String) {
        _route.value = route
    }

    /**
     * Returns the pending route when [signedIn], and clears it either way.
     * Returns null — dropping any pending route rather than merely gating
     * it — when nothing was pending or the caller isn't signed in.
     */
    fun consume(signedIn: Boolean): String? {
        val pending = _route.value
        _route.value = null
        return pending.takeIf { signedIn }
    }

    /** Test-only: drop any pending route so tests don't leak state into each other. */
    internal fun clearForTest() {
        _route.value = null
    }
}
