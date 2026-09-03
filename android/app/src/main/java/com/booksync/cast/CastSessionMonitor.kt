package com.booksync.cast

import android.content.Context
import android.util.Log
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

    private val listener = object : SessionManagerListener<CastSession> {
        override fun onSessionStarted(session: CastSession, sessionId: String) { _isCasting.value = true }
        override fun onSessionResumed(session: CastSession, wasSuspended: Boolean) { _isCasting.value = true }
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
