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

    /** The ephemeral port the server bound to; valid after [open]. */
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
    /** A fresh, not-yet-started server that will gate on the given path token. */
    private val newServer: (pathToken: String) -> CastFileServer,
    private val newToken: () -> String = { UUID.randomUUID().toString().replace("-", "") },
) {
    companion object {
        private const val TAG = "LocalCastServer"
    }

    private var server: CastFileServer? = null

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
        val candidate = newServer(token)
        try {
            candidate.open(readTimeoutMs)
        } catch (e: Exception) {
            Log.e(TAG, "start: failed to start", e)
            return null
        }
        server = candidate
        val bound = CastServerAddress(ip = currentIp, port = candidate.boundPort(), pathToken = token)
        address = bound
        // Host and port only. The path token gates the LAN server that serves
        // the audiobook, so logging it hands anyone reading logcat a working
        // stream URL for as long as the cast session lives (issue #231).
        Log.i(TAG, "LocalCastHttpServer started at http://${bound.ip}:${bound.port}/")
        return bound
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
