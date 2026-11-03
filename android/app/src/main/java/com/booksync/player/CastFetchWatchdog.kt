package com.booksync.player

/**
 * The clock every timestamp handed to [CastFetchWatchdog] comes from: monotonic, and
 * unlike `SystemClock` it also runs in plain JVM tests. Only differences of it mean
 * anything.
 */
fun castClockMs(): Long = System.nanoTime() / 1_000_000L

/** What [CastFetchWatchdog] has concluded about the receiver reaching the phone. */
enum class CastFetchVerdict {
    /** No LOAD acknowledged yet, or the window is still open. */
    WAITING,

    /** A request carrying the session's path token reached the LAN server. */
    FETCHED,

    /** The LOAD was acknowledged and nothing came within the window. */
    TIMED_OUT,

    /** The receiver reported an error after the LOAD. */
    RECEIVER_ERROR,
}

/**
 * Has the Cast receiver come to fetch the file from the phone? (issue #932)
 *
 * A receiver that cannot reach the phone's LAN server (a VLAN that blocks
 * inbound connections, a Wi-Fi firewall) still connects and acknowledges the
 * LOAD, because the control channel is outbound from the phone. It then sits
 * in `LOADING` for about two minutes and fails. This turns that into a
 * decision taken eight seconds after the acknowledgement.
 *
 * Pure on purpose, like [decidePlayerSwitch]: timestamps go in, a verdict
 * comes out. `AudioPlayerService` supplies the clock, schedules the check and
 * acts on the outcome.
 *
 * A request that arrives *before* the acknowledgement is kept for the next
 * acknowledgement: the receiver may probe the URL ahead of its reply reaching
 * the phone. A second acknowledgement is a new LOAD and starts over.
 *
 * Not thread-safe: called from the service's main thread.
 */
class CastFetchWatchdog(val windowMs: Long = DEFAULT_WINDOW_MS) {

    companion object {
        const val DEFAULT_WINDOW_MS = 8_000L
    }

    private var ackedAtMs: Long? = null
    private var fetched = false
    private var receiverError = false

    /** A request seen while no LOAD is pending; applied to the next acknowledgement. */
    private var earlyRequest = false

    fun onLoadAcked(nowMs: Long) {
        ackedAtMs = nowMs
        fetched = earlyRequest
        earlyRequest = false
        receiverError = false
    }

    fun onReceiverRequest(@Suppress("UNUSED_PARAMETER") nowMs: Long) {
        if (ackedAtMs == null) earlyRequest = true else fetched = true
    }

    /** Ignored until a LOAD has been acknowledged: an error from an older load is not this one's. */
    fun onReceiverError() {
        if (ackedAtMs != null) receiverError = true
    }

    fun verdict(nowMs: Long): CastFetchVerdict {
        val acked = ackedAtMs ?: return CastFetchVerdict.WAITING
        return when {
            receiverError -> CastFetchVerdict.RECEIVER_ERROR
            fetched -> CastFetchVerdict.FETCHED
            nowMs - acked >= windowMs -> CastFetchVerdict.TIMED_OUT
            else -> CastFetchVerdict.WAITING
        }
    }

    /** A new Cast session, or the old one ended. */
    fun reset() {
        ackedAtMs = null
        fetched = false
        receiverError = false
        earlyRequest = false
    }
}
