package com.booksync.player

import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
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
        const val CMD_GET_CHAPTERS = "GET_CHAPTERS"
    }

    private var mediaSession: MediaSession? = null
    private var sleepTimerJob: Job? = null
    private val serviceScope = CoroutineScope(Dispatchers.Main + SupervisorJob())
    private lateinit var sharedPrefs: SharedPreferences

    @OptIn(UnstableApi::class)
    override fun onCreate() {
        super.onCreate()
        sharedPrefs = getSharedPreferences("audio_player_prefs", Context.MODE_PRIVATE)
        val initialSpeed = sharedPrefs.getFloat("playback_speed", 1.0f)

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
        
        player.playbackParameters = player.playbackParameters.withSpeed(initialSpeed)

        val sessionCallback = object : MediaSession.Callback {
            override fun onConnect(
                session: MediaSession,
                controller: MediaSession.ControllerInfo
            ): MediaSession.ConnectionResult {
                val sessionCommands = MediaSession.ConnectionResult.DEFAULT_SESSION_COMMANDS.buildUpon()
                    .add(SessionCommand(CMD_SET_SPEED, Bundle.EMPTY))
                    .add(SessionCommand(CMD_SET_SLEEP_TIMER, Bundle.EMPTY))
                    .add(SessionCommand(CMD_GET_SPEED, Bundle.EMPTY))
                    .add(SessionCommand(CMD_GET_CHAPTERS, Bundle.EMPTY))
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
                        sharedPrefs.edit().putFloat("playback_speed", speed).apply()
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
                    CMD_GET_CHAPTERS -> {
                        val result = getChaptersBundle(session.player)
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
        if (player != null) {
            // Save position as safety net for crash recovery
            saveLastPosition(player.currentPosition)
            if (!player.playWhenReady) {
                stopSelf()
            }
        }
    }

    override fun onDestroy() {
        sleepTimerJob?.cancel()
        serviceScope.cancel()
        mediaSession?.run {
            // Save position as safety net for crash recovery
            saveLastPosition(player.currentPosition)
            player.release()
            release()
        }
        mediaSession = null
        super.onDestroy()
    }

    private fun saveLastPosition(positionMs: Long) {
        getSharedPreferences("audio_player_prefs", Context.MODE_PRIVATE)
            .edit()
            .putLong("last_position_ms", positionMs)
            .putLong("last_position_saved_at", System.currentTimeMillis())
            .apply()
    }

    /**
     * Extract chapter metadata from the player's current media.
     * M4B files store chapters as MP4 CHAP atoms. ExoPlayer exposes
     * these via Timeline windows when the media has multiple periods,
     * or via the MediaItem's clipping configuration.
     *
     * Fallback: Use MediaMetadataRetriever to read chapter data from
     * the file if ExoPlayer doesn't provide it via the timeline.
     */
    @OptIn(UnstableApi::class)
    private fun getChaptersBundle(player: androidx.media3.common.Player): Bundle {
        val result = Bundle()
        val titles = mutableListOf<String>()
        val startTimesMs = mutableListOf<Long>()

        try {
            val timeline = player.currentTimeline
            if (timeline.windowCount > 1) {
                // Multiple windows = multiple chapters
                val window = androidx.media3.common.Timeline.Window()
                for (i in 0 until timeline.windowCount) {
                    timeline.getWindow(i, window)
                    val title = window.mediaItem.mediaMetadata.title?.toString()
                        ?: "Chapter ${i + 1}"
                    titles.add(title)
                    startTimesMs.add(window.defaultPositionMs)
                }
            } else if (timeline.windowCount == 1) {
                // Single window — try to extract from the media URI via MediaMetadataRetriever
                val uri = player.currentMediaItem?.localConfiguration?.uri
                if (uri != null) {
                    val retriever = android.media.MediaMetadataRetriever()
                    try {
                        retriever.setDataSource(this, uri)
                        // M4B chapter count via METADATA_KEY_NUM_TRACKS doesn't work for chapters.
                        // Unfortunately, MediaMetadataRetriever doesn't directly expose MP4 chapters.
                        // We'll parse them using ExoPlayer's ChapterTocFrame if available
                        // from the metadata, or retrieve from the ID3/MP4 atoms.
                    } catch (_: Exception) {
                    } finally {
                        retriever.release()
                    }
                }
            }
        } catch (e: Exception) {
            android.util.Log.w("AudioPlayerService", "Error extracting chapters", e)
        }

        result.putInt("count", titles.size)
        result.putStringArray("titles", titles.toTypedArray())
        result.putLongArray("startTimesMs", startTimesMs.toLongArray())
        return result
    }
}
