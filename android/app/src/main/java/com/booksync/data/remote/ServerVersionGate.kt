package com.booksync.data.remote

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import javax.inject.Inject
import javax.inject.Singleton

/**
 * The cached answer to "does the configured server speak our API" (issue #174).
 *
 * Application-scoped, like [FirstRunGate]: the screens that show the verdict are
 * created and destroyed constantly (a tab switch is enough), and re-asking the
 * server every time would put a network round-trip behind navigation for an
 * answer that cannot change while the process lives.
 *
 * Two ways in, one answer out:
 *  - [refreshOnce] — the startup path. Home is the first screen that has both a
 *    server URL and a session, and the only one a returning user reaches without
 *    passing through login, so the check has to be able to start there.
 *  - [record] — the first-run path. "Check connection" already called
 *    `/api/health` on the address the user typed, so the verdict is in its hand;
 *    asking the same server the same question again as Home appears is waste.
 *
 * Never throws and never blocks a screen. A mismatch is a banner, not a wall:
 * the app still works against a server one version out in either direction — the
 * point is that when something *does* break, the user is not left reading a 404.
 */
@Singleton
class ServerVersionGate @Inject constructor(
    private val api: BookSyncApi,
    private val serverUrlManager: ServerUrlManager,
) {

    private val _verdict = MutableStateFlow(VersionCompat.Verdict.Unknown)
    val verdict: StateFlow<VersionCompat.Verdict> = _verdict.asStateFlow()

    /**
     * Whether a server has actually answered this process. Not "have we tried":
     * being offline at launch is not a verdict, and latching it would mean an
     * app started on a train never checks again for the rest of the process.
     */
    @Volatile
    private var answered = false

    /**
     * Ask the configured server, unless something already has.
     *
     * The request carries an absolute URL and so bypasses `BaseUrlInterceptor`,
     * the same shape the first-run probe uses — one code path for "ask this
     * address about its version", regardless of whether the address is stored yet.
     */
    suspend fun refreshOnce() {
        if (answered) return
        val base = normalizeServerUrl(serverUrlManager.currentUrl) ?: return
        val health = try {
            api.getHealth("$base/api/health")
        } catch (e: Exception) {
            // Unreachable, HTML from a captive portal, a 503 while the database
            // is down: all ordinary, none of them a version verdict. Stay
            // Unknown — which shows nothing — and let the next screen retry.
            return
        }
        record(health)
    }

    /** Settle the verdict from a `/api/health` body someone else already fetched. */
    fun record(health: HealthResponse) {
        answered = true
        _verdict.value = VersionCompat.compare(health.api_version)
    }
}
