package com.booksync.player

import android.util.Log
import java.util.UUID

/**
 * The slice of `LocalCastHttpServer` the controller drives, so its lifecycle
 * can be tested without binding a socket. Deliberately not named after
 * NanoHTTPD's own `start(int)` / `stop()`: NanoHTTPD's `start(int)` starts a
 * *daemon* thread, and a same-named interface method would have been
 * satisfied by it silently.
 */
interface CastFileServer {
    /** Binds and starts serving. Throws if the socket cannot be opened. */
    @Throws(java.io.IOException::class)
    fun open(readTimeoutMs: Int)

    fun close()

    /** The port the server actually bound to (the requested one, or the one chosen for port 0); valid after [open]. */
    fun boundPort(): Int
}

/**
 * Owns the phone's LAN cast server for the life of a Cast session (issue #225):
 * when to start it, when to leave it alone, when to restart it, and the
 * per-session path token that gates it.
 *
 * `AudioPlayerService` used to hold this as four nullable fields and two
 * methods; the rules were only ever exercised by casting to a real speaker.
 * Everything Android-specific — the Wi-Fi address walk and the NanoHTTPD
 * subclass — is injected, so this is plain Kotlin.
 *
 * Not thread-safe: called from the service's main thread. [address] is
 * volatile only because the Cast-resumption path reads it from an IO
 * coroutine, as it always did.
 */
class LocalCastServerController(
    private val readTimeoutMs: Int,
    /** The phone's Wi-Fi IPv4, or null when there is no Wi-Fi network. */
    private val detectIp: () -> String?,
    /**
     * The port to try first: the Account-screen cast port (issue #931), read at
     * each start so a change applies to the next Cast session. 0 means "any
     * free port".
     */
    private val preferredPort: () -> Int,
    /**
     * A fresh, not-yet-started server that will gate on the given path token
     * and bind the given port (0 = any free port). It calls the third argument,
     * with a monotonic timestamp, whenever a request carrying the token arrives
     * (issue #932) -- on the server's own thread, not the main one.
     */
    private val newServer: (pathToken: String, port: Int, onTokenMatchedRequest: (Long) -> Unit) -> CastFileServer,
    private val newToken: () -> String = { UUID.randomUUID().toString().replace("-", "") },
) {
    companion object {
        private const val TAG = "LocalCastServer"
    }

    private var server: CastFileServer? = null

    /**
     * Told when the receiver fetches from the phone (issue #932). Set by the
     * service; read on the server thread, so volatile. Looked up per request
     * rather than captured, so it can be installed after [start].
     */
    @Volatile
    var onReceiverRequest: ((nowMs: Long) -> Unit)? = null

    /** Where the receiver should fetch from, or null when nothing is serving. */
    @Volatile
    var address: CastServerAddress? = null
        private set

    val isRunning: Boolean get() = address != null

    /**
     * Boots the server, or leaves it running when it is already bound to the
     * current Wi-Fi address. Idempotent, and safe to call on every session
     * start/resume: a phone that changed networks gets a clean restart on the
     * new address. Returns the address the receiver should use, or null.
     */
    fun start(): CastServerAddress? {
        val currentIp = detectIp()
        if (currentIp == null) {
            Log.w(TAG, "start: no Wi-Fi IPv4 found — Cast streaming will fail")
            stop()
            return null
        }
        address?.let { running ->
            if (running.ip == currentIp) {
                Log.d(TAG, "start: already running at http://${running.ip}:${running.port}/")
                return running
            }
        }
        // IP changed or nothing running — restart cleanly, with a new token.
        stop()

        val token = newToken()
        val preferred = preferredPort()
        var usingFallback = false
        var candidate = newServer(token, preferred, ::notifyReceiverRequest)
        try {
            candidate.open(readTimeoutMs)
        } catch (e: Exception) {
            if (preferred == 0) {
                Log.e(TAG, "start: failed to start", e)
                return null
            }
            // The cast port is busy (or refused). A network that needs no
            // firewall rule should not lose casting over it, so take any free
            // port -- once. The address says it happened (issue #931).
            Log.w(TAG, "start: port $preferred unavailable, falling back to an ephemeral port", e)
            usingFallback = true
            candidate = newServer(token, 0, ::notifyReceiverRequest)
            try {
                candidate.open(readTimeoutMs)
            } catch (e2: Exception) {
                Log.e(TAG, "start: failed to start", e2)
                return null
            }
        }
        server = candidate
        val bound = CastServerAddress(
            ip = currentIp,
            port = candidate.boundPort(),
            pathToken = token,
            usingFallbackPort = usingFallback,
        )
        address = bound
        // Host and port only. The path token gates the LAN server that serves
        // the audiobook, so logging it hands anyone reading logcat a working
        // stream URL for as long as the cast session lives (issue #231).
        Log.i(TAG, "LocalCastHttpServer started at http://${bound.ip}:${bound.port}/")
        return bound
    }

    private fun notifyReceiverRequest(nowMs: Long) {
        onReceiverRequest?.invoke(nowMs)
    }

    /** Tears the server down. Safe to call when nothing is running. */
    fun stop() {
        server?.let { running ->
            try {
                running.close()
                Log.i(TAG, "LocalCastHttpServer stopped")
            } catch (e: Exception) {
                Log.w(TAG, "LocalCastHttpServer stop threw", e)
            }
        }
        server = null
        address = null
    }
}
