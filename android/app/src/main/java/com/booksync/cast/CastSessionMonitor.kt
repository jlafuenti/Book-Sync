package com.booksync.cast

import android.content.Context
import android.util.Log
import com.booksync.player.CastFailure
import com.booksync.player.CastServerAddress
import com.google.android.gms.cast.framework.CastContext
import com.google.android.gms.cast.framework.CastSession
import com.google.android.gms.cast.framework.SessionManagerListener
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Is a Cast session live right now?
 *
 * `AudioPlayerService` has always known this — it owns the session listener that
 * swaps ExoPlayer for CastPlayer — but nothing outside the service could ask.
 * The player screen needs to, because Cast is the one surface that still
 * requires a downloaded file: `LocalCastHttpServer` streams the phone's copy to
 * the receiver over the LAN, and the receiver cannot send a Bearer token, so
 * there is no server-side Cast path (issue #171).
 *
 * It also carries two things from the service to the screens (issues #931, #932):
 * the address the LAN cast server is serving from, which the Account screen uses
 * to say when the cast port was busy, and the [castFailure] the service publishes
 * when it ends a Cast session because the speaker never reached the phone.
 *
 * Defensive throughout: Cast is unavailable on some devices (Amazon Fire), which
 * is why `BookSyncApp` initialises `CastContext` inside a try/catch. Failing to
 * observe simply leaves this reporting "not casting", which is the same answer
 * a device with no Cast support would give anyway.
 */
@Singleton
class CastSessionMonitor @Inject constructor(
    @param:ApplicationContext private val context: Context,
) {
    private val _isCasting = MutableStateFlow(false)
    val isCasting: StateFlow<Boolean> = _isCasting.asStateFlow()

    private val _castFailure = MutableStateFlow<CastFailure?>(null)

    /** Why the last Cast session was ended by the app, until the listener dismisses it or a new session starts. */
    val castFailure: StateFlow<CastFailure?> = _castFailure.asStateFlow()

    private val _serverAddress = MutableStateFlow<CastServerAddress?>(null)

    /** Where the LAN cast server is serving right now, or null when it is not running. */
    val serverAddress: StateFlow<CastServerAddress?> = _serverAddress.asStateFlow()

    /** Called by `AudioPlayerService` when it ends a Cast session the speaker could not play from. */
    fun report(failure: CastFailure) { _castFailure.value = failure }

    fun clear() { _castFailure.value = null }

    /** Called by `AudioPlayerService` as the LAN cast server starts and stops. */
    fun setServerAddress(address: CastServerAddress?) { _serverAddress.value = address }

    private val listener = object : SessionManagerListener<CastSession> {
        override fun onSessionStarted(session: CastSession, sessionId: String) {
            _castFailure.value = null
            _isCasting.value = true
        }
        override fun onSessionResumed(session: CastSession, wasSuspended: Boolean) {
            _castFailure.value = null
            _isCasting.value = true
        }
        override fun onSessionEnded(session: CastSession, error: Int) { _isCasting.value = false }
        override fun onSessionSuspended(session: CastSession, reason: Int) { _isCasting.value = false }
        override fun onSessionStartFailed(session: CastSession, error: Int) { _isCasting.value = false }
        override fun onSessionResumeFailed(session: CastSession, error: Int) { _isCasting.value = false }
        override fun onSessionEnding(session: CastSession) {}
        override fun onSessionResuming(session: CastSession, sessionId: String) {}
        override fun onSessionStarting(session: CastSession) {}
    }

    init {
        try {
            // Returns the singleton BookSyncApp already created; never re-initialises.
            val sessions = CastContext.getSharedInstance(context).sessionManager
            _isCasting.value = sessions.currentCastSession?.isConnected == true
            sessions.addSessionManagerListener(listener, CastSession::class.java)
        } catch (e: Exception) {
            Log.d("CastSessionMonitor", "Cast not available: ${e.message}")
        }
    }
}
