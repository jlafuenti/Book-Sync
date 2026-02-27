package com.booksync.player

import android.content.Intent
import android.os.Bundle
import androidx.annotation.OptIn
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.session.MediaSession
import androidx.media3.session.MediaSessionService
import androidx.media3.session.SessionCommand
import androidx.media3.session.SessionResult
import com.google.common.util.concurrent.Futures
import com.google.common.util.concurrent.ListenableFuture
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Foreground media playback service using Media3.
 * Handles audiobook playback in the background, with media session
 * support for lock screen controls and Android Auto.
 *
 * Custom session commands:
 *  - SET_SPEED(speed: Float)
 *  - SET_SLEEP_TIMER(minutes: Int) — 0 to cancel
 */
@AndroidEntryPoint
class AudioPlayerService : MediaSessionService() {

    companion object {
        const val CMD_SET_SPEED = "SET_SPEED"
        const val CMD_SET_SLEEP_TIMER = "SET_SLEEP_TIMER"
        const val CMD_GET_SPEED = "GET_SPEED"
    }

    private var mediaSession: MediaSession? = null
    private var sleepTimerJob: Job? = null
    private val serviceScope = CoroutineScope(Dispatchers.Main + SupervisorJob())

    @OptIn(UnstableApi::class)
    override fun onCreate() {
        super.onCreate()
        val player = ExoPlayer.Builder(this)
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setContentType(C.AUDIO_CONTENT_TYPE_SPEECH)
                    .setUsage(C.USAGE_MEDIA)
                    .build(),
                /* handleAudioFocus = */ true,
            )
            .setHandleAudioBecomingNoisy(true)
            .build()

        val sessionCallback = object : MediaSession.Callback {
            override fun onConnect(
                session: MediaSession,
                controller: MediaSession.ControllerInfo
            ): MediaSession.ConnectionResult {
                val sessionCommands = MediaSession.ConnectionResult.DEFAULT_SESSION_COMMANDS.buildUpon()
                    .add(SessionCommand(CMD_SET_SPEED, Bundle.EMPTY))
                    .add(SessionCommand(CMD_SET_SLEEP_TIMER, Bundle.EMPTY))
                    .add(SessionCommand(CMD_GET_SPEED, Bundle.EMPTY))
                    .build()
                return MediaSession.ConnectionResult.AcceptedResultBuilder(session)
                    .setAvailableSessionCommands(sessionCommands)
                    .build()
            }

            override fun onCustomCommand(
                session: MediaSession,
                controller: MediaSession.ControllerInfo,
                customCommand: SessionCommand,
                args: Bundle
            ): ListenableFuture<SessionResult> {
                when (customCommand.customAction) {
                    CMD_SET_SPEED -> {
                        val speed = args.getFloat("speed", 1.0f)
                        session.player.playbackParameters =
                            session.player.playbackParameters.withSpeed(speed)
                        return Futures.immediateFuture(SessionResult(SessionResult.RESULT_SUCCESS))
                    }
                    CMD_SET_SLEEP_TIMER -> {
                        val minutes = args.getInt("minutes", 0)
                        handleSleepTimer(minutes)
                        return Futures.immediateFuture(SessionResult(SessionResult.RESULT_SUCCESS))
                    }
                    CMD_GET_SPEED -> {
                        val result = Bundle()
                        result.putFloat("speed", session.player.playbackParameters.speed)
                        return Futures.immediateFuture(SessionResult(SessionResult.RESULT_SUCCESS, result))
                    }
                }
                return Futures.immediateFuture(SessionResult(SessionResult.RESULT_ERROR_NOT_SUPPORTED))
            }
        }

        mediaSession = MediaSession.Builder(this, player)
            .setId("AudioPlayerSession")
            .setCallback(sessionCallback)
            .build()
    }

    private fun handleSleepTimer(minutes: Int) {
        sleepTimerJob?.cancel()
        if (minutes <= 0) return

        sleepTimerJob = serviceScope.launch {
            val totalMs = minutes * 60 * 1000L
            // Wait until 30 seconds before end for fade-out
            val waitMs = maxOf(0L, totalMs - 30_000L)
            delay(waitMs)

            // Fade out over 30 seconds (or remaining time if less)
            val fadeSteps = 30
            val fadeDuration = minOf(30_000L, totalMs)
            val fadeInterval = fadeDuration / fadeSteps
            val player = mediaSession?.player ?: return@launch
            val originalVolume = 1.0f

            for (i in fadeSteps downTo 0) {
                if (!player.isPlaying) return@launch
                player.volume = originalVolume * i / fadeSteps
                delay(fadeInterval)
            }
            player.pause()
            player.volume = originalVolume
        }
    }

    override fun onGetSession(controllerInfo: MediaSession.ControllerInfo): MediaSession? {
        return mediaSession
    }

    override fun onTaskRemoved(rootIntent: Intent?) {
        val player = mediaSession?.player
        if (player != null && !player.playWhenReady) {
            stopSelf()
        }
    }

    override fun onDestroy() {
        sleepTimerJob?.cancel()
        serviceScope.cancel()
        mediaSession?.run {
            player.release()
            release()
        }
        mediaSession = null
        super.onDestroy()
    }
}
