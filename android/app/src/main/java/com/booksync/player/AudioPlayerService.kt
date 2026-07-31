package com.booksync.player

import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.net.ConnectivityManager
import android.net.Uri
import android.os.Bundle
import android.util.Log
import androidx.annotation.OptIn
import androidx.media3.cast.CastPlayer
import androidx.media3.cast.MediaItemConverter
import androidx.media3.cast.SessionAvailabilityListener
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.ForwardingPlayer
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.MimeTypes
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.session.LibraryResult
import androidx.media3.session.MediaLibraryService
import androidx.media3.session.MediaSession
import androidx.media3.session.SessionCommand
import androidx.media3.session.SessionResult
import com.booksync.auto.CoverArtHelper
import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.diagnostics.LogChannel
import com.booksync.data.remote.TokenManager
import com.booksync.data.repository.BookSyncRepository
import com.google.android.gms.cast.framework.CastContext
import com.google.android.gms.cast.framework.CastSession
import com.google.android.gms.cast.framework.SessionManagerListener
import com.google.common.collect.ImmutableList
import com.google.common.util.concurrent.Futures
import com.google.common.util.concurrent.ListenableFuture
import com.google.common.util.concurrent.SettableFuture
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.firstOrNull
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import java.io.File
import java.net.Inet4Address
import java.util.UUID
import javax.inject.Inject
import kotlinx.coroutines.withTimeoutOrNull

/**
 * How long a resume waits for the server's position before falling back to
 * the local cache. Mirrors PlayerScreen's bound.
 */
private const val SERVER_POSITION_TIMEOUT_MS = 1500L

/**
 * Foreground media playback service using Media3 MediaLibraryService.
 *
 * Serves as the single media service for both the phone app (via MediaController)
 * and Android Auto (via MediaLibrarySession browsing). A single ExoPlayer instance
 * is shared between both connections.
 *
 * Browse tree:
 *   [root]
 *   ├── continue_listening  — downloaded books with progress, ordered by most recently played
 *   └── library             — all downloaded audiobooks, alphabetical
 *
 * Custom session commands (phone app):
 *   SET_SPEED(speed: Float)
 *   SET_SLEEP_TIMER(minutes: Int) — 0 to cancel
 *   GET_SPEED
 *   GET_CHAPTERS
 */
@AndroidEntryPoint
class AudioPlayerService : MediaLibraryService() {

    companion object {
        const val CMD_SET_SPEED = "SET_SPEED"
        const val CMD_SET_SLEEP_TIMER = "SET_SLEEP_TIMER"
        const val CMD_GET_SPEED = "GET_SPEED"
        const val CMD_GET_CHAPTERS = "GET_CHAPTERS"

        private const val TAG = "AudioPlayerService"
        private const val PREFS_NAME = "audio_player_prefs"
        private const val PREF_SPEED = "playback_speed"
        private const val PREF_LAST_POSITION = "last_position_ms"
        private const val PREF_LAST_MEDIA_ID = "last_media_id"
        private const val PREF_LAST_SAVED_AT = "last_position_saved_at"
        private const val AUTO_SAVE_INTERVAL_MS = 5_000L
        // Generous socket read timeout for slow Cast receivers downloading big audiobook files.
        private const val NanoHTTPDSocketReadTimeoutMs = 60_000
        // Write a history-log entry every 30 min of continuous playback
        // (in addition to pause/stop boundaries). Keeps the history tab scannable.
        private const val AUTO_LOG_INTERVAL_MS = 30L * 60L * 1000L
    }

    @Inject lateinit var repository: BookSyncRepository
    @Inject lateinit var coverArtHelper: CoverArtHelper
    @Inject lateinit var tokenManager: TokenManager
    @Inject lateinit var serverUrlManager: com.booksync.data.remote.ServerUrlManager
    @Inject lateinit var diagnosticLogger: com.booksync.diagnostics.DiagnosticLogger

    private var mediaLibrarySession: MediaLibrarySession? = null
    private var castPlayer: CastPlayer? = null
    private var exoPlayer: Player? = null
    private var sleepTimerJob: Job? = null
    private var autoPositionSaveJob: Job? = null
    // Last time we wrote a history-log entry (appendToLog=true). Updated on pause,
    // track-end, service destroy, cast session transitions, and the 30-min tick.
    // Only advanced while isPlaying (the polling loop only runs then), so pauses
    // naturally freeze the 30-min clock.
    private var lastAutoLogTimeMs = 0L
    private val serviceScope = CoroutineScope(Dispatchers.Main + SupervisorJob())
    private lateinit var sharedPrefs: SharedPreferences

    // Local HTTP server that serves downloaded audiobooks to the Cast receiver over the LAN.
    // Started in castSessionListener.onSessionStarted, torn down in onSessionEnded. Avoids
    // depending on public DNS for tandem.lafuenti.com — Google Home devices hardcode 8.8.8.8.
    private var localCastServer: LocalCastHttpServer? = null
    private var localCastIp: String? = null
    private var localCastPort: Int? = null
    private var localCastPathToken: String? = null
    // The local (file://) MediaItem we were playing when casting started. Remembered so that
    // when the Cast session ends we can restore local playback — CastPlayer's own
    // currentMediaItem carries the http:// LAN URL (we bypass setMediaItem), which ExoPlayer
    // cannot play (cleartext blocked) and whose mediaId we can't map back to a file.
    private var lastLocalMediaItem: MediaItem? = null
    // Last position (ms) the Cast receiver reported. When a Cast session ends, CastPlayer's
    // currentPosition reads 0 (already disconnected), so we restore local playback from this
    // value instead of jumping back to 0:00.
    private var lastKnownCastPositionMs: Long = 0L

    private val remoteMediaClientCallback = object : com.google.android.gms.cast.framework.media.RemoteMediaClient.Callback() {
        override fun onStatusUpdated() {
            val client = CastContext.getSharedInstance()?.sessionManager?.currentCastSession?.remoteMediaClient
            val status = client?.mediaStatus ?: return
            val stateName = when (status.playerState) {
                com.google.android.gms.cast.MediaStatus.PLAYER_STATE_UNKNOWN -> "UNKNOWN"
                com.google.android.gms.cast.MediaStatus.PLAYER_STATE_IDLE -> "IDLE"
                com.google.android.gms.cast.MediaStatus.PLAYER_STATE_PLAYING -> "PLAYING"
                com.google.android.gms.cast.MediaStatus.PLAYER_STATE_PAUSED -> "PAUSED"
                com.google.android.gms.cast.MediaStatus.PLAYER_STATE_BUFFERING -> "BUFFERING"
                com.google.android.gms.cast.MediaStatus.PLAYER_STATE_LOADING -> "LOADING"
                else -> "state=${status.playerState}"
            }
            val idleReasonName = when (status.idleReason) {
                com.google.android.gms.cast.MediaStatus.IDLE_REASON_NONE -> "none"
                com.google.android.gms.cast.MediaStatus.IDLE_REASON_FINISHED -> "FINISHED"
                com.google.android.gms.cast.MediaStatus.IDLE_REASON_CANCELED -> "CANCELED"
                com.google.android.gms.cast.MediaStatus.IDLE_REASON_INTERRUPTED -> "INTERRUPTED"
                com.google.android.gms.cast.MediaStatus.IDLE_REASON_ERROR -> "ERROR"
                else -> "reason=${status.idleReason}"
            }
            val pos = status.streamPosition
            if (pos > 0L) lastKnownCastPositionMs = pos
            Log.d(TAG, "Cast status: $stateName idle=$idleReasonName pos=$pos")
        }

        override fun onMediaError(error: com.google.android.gms.cast.MediaError) {
            Log.e(
                TAG,
                "Cast onMediaError: type=${error.type} reason=${error.reason} detailedCode=${error.detailedErrorCode}"
            )
        }
    }

    private fun attachRemoteMediaClientCallback() {
        try {
            val client = CastContext.getSharedInstance()
                ?.sessionManager?.currentCastSession?.remoteMediaClient
            client?.registerCallback(remoteMediaClientCallback)
        } catch (e: Exception) {
            Log.w(TAG, "attachRemoteMediaClientCallback failed", e)
        }
    }

    private fun detachRemoteMediaClientCallback() {
        try {
            val client = CastContext.getSharedInstance()
                ?.sessionManager?.currentCastSession?.remoteMediaClient
            client?.unregisterCallback(remoteMediaClientCallback)
        } catch (_: Exception) {}
    }

    private val castSessionListener = object : SessionManagerListener<CastSession> {
        override fun onSessionStarted(session: CastSession, sessionId: String) {
            attachRemoteMediaClientCallback()
            startLocalCastServer()
            castPlayer?.let { switchToPlayer(it, savePosition = true) }
        }
        override fun onSessionResumed(session: CastSession, wasSuspended: Boolean) {
            attachRemoteMediaClientCallback()
            startLocalCastServer()
            castPlayer?.let { switchToPlayer(it, savePosition = false) }
        }
        override fun onSessionEnded(session: CastSession, error: Int) {
            detachRemoteMediaClientCallback()
            exoPlayer?.let { switchToPlayer(it, savePosition = true) }
            stopLocalCastServer()
        }
        override fun onSessionSuspended(session: CastSession, reason: Int) {
            detachRemoteMediaClientCallback()
            exoPlayer?.let { switchToPlayer(it, savePosition = true) }
            stopLocalCastServer()
        }
        override fun onSessionStartFailed(session: CastSession, error: Int) {}
        override fun onSessionEnding(session: CastSession) {}
        override fun onSessionResumeFailed(session: CastSession, error: Int) {}
        override fun onSessionResuming(session: CastSession, sessionId: String) {}
        override fun onSessionStarting(session: CastSession) {}
    }

    @OptIn(UnstableApi::class)
    override fun onCreate() {
        super.onCreate()
        Log.i(TAG, "onCreate — service starting")
        sharedPrefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        val initialSpeed = sharedPrefs.getFloat(PREF_SPEED, 1.0f)

        val playerListener = object : Player.Listener {
            override fun onIsPlayingChanged(isPlaying: Boolean) {
                if (isPlaying) {
                    startAutoPositionSave()
                } else {
                    stopAutoPositionSave()
                    // Pause is a session boundary — log it. Also covers the
                    // sleep-timer path, which drops playWhenReady to false.
                    saveCurrentPositionForAuto(appendToLog = true)
                }
            }
            override fun onPlaybackStateChanged(playbackState: Int) {
                // Track/audiobook reached its natural end (fires on both
                // ExoPlayer and CastPlayer since the same listener is attached
                // to both). Log as a session boundary.
                if (playbackState == Player.STATE_ENDED) {
                    saveCurrentPositionForAuto(appendToLog = true)
                    val player = mediaLibrarySession?.player ?: return
                    val mediaId = player.currentMediaItem?.mediaId ?: return
                    serviceScope.launch {
                        try {
                            when {
                                mediaId.startsWith("pair_") -> {
                                    val pairId = mediaId.removePrefix("pair_").toIntOrNull() ?: return@launch
                                    val pair = repository.getPairById(pairId) ?: return@launch
                                    repository.markComplete("audiobook", pair.audiobookId)
                                    repository.markComplete("ebook", pair.ebookId)
                                }
                                mediaId.startsWith("audiobook_") -> {
                                    val audiobookId = mediaId.removePrefix("audiobook_").toIntOrNull() ?: return@launch
                                    repository.markComplete("audiobook", audiobookId)
                                }
                            }
                        } catch (e: Exception) {
                            Log.w(TAG, "Failed to mark complete on end", e)
                        }
                    }
                }
            }
        }

        val localPlayer = ExoPlayer.Builder(this)
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setContentType(C.AUDIO_CONTENT_TYPE_SPEECH)
                    .setUsage(C.USAGE_MEDIA)
                    .build(),
                /* handleAudioFocus = */ true,
            )
            .setHandleAudioBecomingNoisy(true)
            .setSeekBackIncrementMs(10_000)
            .setSeekForwardIncrementMs(10_000)
            .build()
        localPlayer.playbackParameters = localPlayer.playbackParameters.withSpeed(initialSpeed)
        // Android Auto on some head units renders rewind/fast-forward buttons based on
        // SEEK_TO_PREVIOUS/SEEK_TO_NEXT rather than SEEK_BACK/SEEK_FORWARD.
        // We wrap the player so those "track-style" commands behave like +/-10s seeking.
        val androidAutoPlayer = AndroidAutoSeekMappingPlayer(localPlayer)
        androidAutoPlayer.addListener(playerListener)
        exoPlayer = androidAutoPlayer

        mediaLibrarySession = MediaLibrarySession.Builder(this, androidAutoPlayer, BrowseCallback())
            .setId("AudioPlayerSession")
            .build()

        // Hook up CastPlayer if Cast SDK was successfully initialized (in BookSyncApp).
        // CastContext.getSharedInstance() is safe here — it only returns the existing singleton
        // initialized by BookSyncApp; it never re-initializes.
        try {
            val castContext = CastContext.getSharedInstance() ?: return
            // Custom converter ensures the Cast LOAD command uses the HTTPS stream URL as
            // contentId. DefaultMediaItemConverter uses mediaItem.mediaId (e.g. "pair_62"),
            // which the Default Media Receiver rejects as INVALID_PARAMS (2001).
            val cast = CastPlayer(castContext, BookSyncCastMediaItemConverter())
            cast.addListener(playerListener)
            cast.setSessionAvailabilityListener(object : SessionAvailabilityListener {
                override fun onCastSessionAvailable() {}
                override fun onCastSessionUnavailable() {}
            })
            castPlayer = cast
            castContext.sessionManager.addSessionManagerListener(
                castSessionListener, CastSession::class.java
            )
            // If a cast session is already active when the service starts, switch immediately
            if (castContext.sessionManager.currentCastSession?.isConnected == true) {
                switchToPlayer(cast, savePosition = false)
            }
        } catch (e: Exception) {
            Log.d(TAG, "Cast not available: ${e.message}")
        }
    }

    /**
     * Maps Android Auto "previous/next" controls to relative seek backward/forward.
     *
     * This lets head units that only advertise `COMMAND_SEEK_TO_PREVIOUS/NEXT` still get the
     * expected rewind/fast-forward behavior (10s, driven by ExoPlayer's seek increment setup).
     */
    private class AndroidAutoSeekMappingPlayer(delegate: Player) : ForwardingPlayer(delegate) {
        override fun getAvailableCommands(): Player.Commands {
            val base = super.getAvailableCommands()
            return base.buildUpon()
                .add(Player.COMMAND_SEEK_TO_PREVIOUS)
                .add(Player.COMMAND_SEEK_TO_NEXT)
                .add(Player.COMMAND_SEEK_TO_PREVIOUS_MEDIA_ITEM)
                .add(Player.COMMAND_SEEK_TO_NEXT_MEDIA_ITEM)
                .build()
        }

        override fun hasPreviousMediaItem(): Boolean = true
        @Suppress("OVERRIDE_DEPRECATION") override fun hasNext(): Boolean = true
        override fun hasNextMediaItem(): Boolean = true

        override fun seekToPrevious() { seekBack() }
        override fun seekToNext() { seekForward() }
        override fun seekToPreviousMediaItem() { seekBack() }
        override fun seekToNextMediaItem() { seekForward() }
        @Suppress("OVERRIDE_DEPRECATION") override fun seekToPreviousWindow() { seekBack() }
        @Suppress("OVERRIDE_DEPRECATION") override fun seekToNextWindow() { seekForward() }
    }

    /**
     * Builds Cast LOAD commands that the Default Media Receiver (CC1AD845) will accept.
     *
     * Media3's DefaultMediaItemConverter sets `contentId = mediaItem.mediaId` (e.g. "pair_62"),
     * which the Default Media Receiver rejects as INVALID_PARAMS (statusCode 2001). The receiver
     * requires contentId to look like a URL. We override the converter to put the HTTPS stream
     * URL in both contentId AND contentUrl, and copy over the minimal metadata the receiver
     * actually consumes (title, artist, artwork).
     */
    private class BookSyncCastMediaItemConverter : MediaItemConverter {
        override fun toMediaQueueItem(mediaItem: MediaItem): com.google.android.gms.cast.MediaQueueItem {
            val localConfig = checkNotNull(mediaItem.localConfiguration) {
                "MediaItem must have localConfiguration for casting"
            }
            val mimeType = checkNotNull(localConfig.mimeType) {
                "MediaItem must have a mimeType for casting"
            }
            val url = localConfig.uri.toString()

            val castMetadata = com.google.android.gms.cast.MediaMetadata(
                com.google.android.gms.cast.MediaMetadata.MEDIA_TYPE_MUSIC_TRACK
            )
            mediaItem.mediaMetadata.title?.let {
                castMetadata.putString(
                    com.google.android.gms.cast.MediaMetadata.KEY_TITLE,
                    it.toString()
                )
            }
            mediaItem.mediaMetadata.artist?.let {
                castMetadata.putString(
                    com.google.android.gms.cast.MediaMetadata.KEY_ARTIST,
                    it.toString()
                )
            }
            mediaItem.mediaMetadata.artworkUri?.let {
                castMetadata.addImage(com.google.android.gms.common.images.WebImage(it))
            }

            val mediaInfo = com.google.android.gms.cast.MediaInfo.Builder(url)
                .setStreamType(com.google.android.gms.cast.MediaInfo.STREAM_TYPE_BUFFERED)
                .setContentType(mimeType)
                .setContentUrl(url)
                .setMetadata(castMetadata)
                .build()

            // No verbose logging here — this path is now defensive only; Cast LOADs go through
            // sendDirectCastLoad which bypasses CastPlayer.setMediaItem entirely.
            return com.google.android.gms.cast.MediaQueueItem.Builder(mediaInfo).build()
        }

        override fun toMediaItem(mediaQueueItem: com.google.android.gms.cast.MediaQueueItem): MediaItem {
            val info = checkNotNull(mediaQueueItem.media) {
                "MediaQueueItem must have a MediaInfo"
            }
            val uri = info.contentUrl ?: info.contentId
            return MediaItem.Builder()
                .setUri(uri)
                .setMimeType(info.contentType)
                .build()
        }
    }

    override fun onGetSession(controllerInfo: MediaSession.ControllerInfo): MediaLibrarySession? {
        Log.i(TAG, "onGetSession — pkg=${controllerInfo.packageName}, session=${if (mediaLibrarySession != null) "ok" else "NULL"}")
        return mediaLibrarySession
    }

    override fun onBind(intent: android.content.Intent?): android.os.IBinder? {
        val pkg = intent?.getStringExtra("android.media.session.CONTROLLER_PACKAGE_NAME") ?: intent?.`package` ?: "unknown"
        diagnosticLogger.i(LogChannel.AUTO, TAG, "onBind pkg=$pkg action=${intent?.action}")
        return super.onBind(intent)
    }

    override fun onTaskRemoved(rootIntent: Intent?) {
        val player = mediaLibrarySession?.player
        if (player != null) {
            saveLastPosition(player.currentPosition, player.currentMediaItem?.mediaId)
            // Note: we don't log a history entry here. If the player was
            // playing, the OS will stop it → onIsPlayingChanged(false) logs.
            // If it was already paused, that pause already logged. Adding
            // a log here would double-log in the common "pause, close app" flow.
            if (!player.playWhenReady) {
                stopSelf()
            }
        }
    }

    override fun onDestroy() {
        sleepTimerJob?.cancel()
        stopAutoPositionSave()
        stopLocalCastServer()
        serviceScope.cancel()
        try {
            CastContext.getSharedInstance()?.sessionManager
                ?.removeSessionManagerListener(castSessionListener, CastSession::class.java)
        } catch (_: Exception) {}
        castPlayer?.setSessionAvailabilityListener(null)
        castPlayer?.release()
        castPlayer = null
        mediaLibrarySession?.run {
            saveLastPosition(player.currentPosition, player.currentMediaItem?.mediaId)
            player.release()
            release()
        }
        mediaLibrarySession = null
        exoPlayer = null
        super.onDestroy()
    }

    // =========================================================
    // Local Cast HTTP server
    // =========================================================

    /**
     * Boots the on-device HTTP server that streams downloaded audiobooks to the Cast
     * receiver over the LAN. Returns true if the server is up and we have a usable
     * Wi-Fi IPv4 address to put in Cast URLs. Idempotent — safe to call when already
     * running (will rebind to the current Wi-Fi IP, which is what we want if the phone
     * just changed networks).
     */
    private fun startLocalCastServer() {
        // If we already have a server running and the IP hasn't changed, leave it alone.
        val currentIp = detectWifiIpv4()
        if (currentIp == null) {
            Log.w(TAG, "startLocalCastServer: no Wi-Fi IPv4 found — Cast streaming will fail")
            stopLocalCastServer()
            return
        }
        if (localCastServer != null && localCastIp == currentIp) {
            Log.d(TAG, "startLocalCastServer: already running at http://$currentIp:$localCastPort/")
            return
        }
        // IP changed or no server yet — restart cleanly.
        stopLocalCastServer()

        val token = UUID.randomUUID().toString().replace("-", "")
        val server = LocalCastHttpServer(
            audiobooksDir = File(filesDir, "audiobooks"),
            pathToken = token,
        )
        try {
            server.start(NanoHTTPDSocketReadTimeoutMs, /* daemon = */ false)
        } catch (e: Exception) {
            Log.e(TAG, "startLocalCastServer: failed to start", e)
            return
        }
        localCastServer = server
        localCastIp = currentIp
        localCastPort = server.listeningPort
        localCastPathToken = token
        Log.i(TAG, "LocalCastHttpServer started at http://$currentIp:${server.listeningPort}/$token/")
    }

    private fun stopLocalCastServer() {
        localCastServer?.let { server ->
            try {
                server.stop()
                Log.i(TAG, "LocalCastHttpServer stopped")
            } catch (e: Exception) {
                Log.w(TAG, "LocalCastHttpServer stop threw", e)
            }
        }
        localCastServer = null
        localCastIp = null
        localCastPort = null
        localCastPathToken = null
    }

    /**
     * Finds the phone's Wi-Fi IPv4 by walking the active network's LinkProperties.
     * Returns null if there's no Wi-Fi network (e.g. cellular-only).
     */
    private fun detectWifiIpv4(): String? {
        val cm = getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
            ?: return null
        // Walk every network — cast typically requires Wi-Fi, but a multi-homed phone on
        // Wi-Fi + cellular might have a non-default Wi-Fi network we still want to use.
        for (network in cm.allNetworks) {
            val caps = cm.getNetworkCapabilities(network) ?: continue
            if (!caps.hasTransport(android.net.NetworkCapabilities.TRANSPORT_WIFI)) continue
            val props = cm.getLinkProperties(network) ?: continue
            for (linkAddress in props.linkAddresses) {
                val addr = linkAddress.address
                if (addr is Inet4Address && !addr.isLoopbackAddress && !addr.isAnyLocalAddress) {
                    return addr.hostAddress
                }
            }
        }
        return null
    }


    // =========================================================
    // Cast player switching
    // =========================================================

    /**
     * Switches the active player between ExoPlayer (local) and CastPlayer (Chromecast).
     * Saves the current position before switching if [savePosition] is true, then
     * transfers the current media item and position to the new player.
     */
    private fun switchToPlayer(newPlayer: Player, savePosition: Boolean) {
        val session = mediaLibrarySession ?: return
        val currentPlayer = session.player
        if (currentPlayer === newPlayer) return

        // Every cast transition is a session boundary — log it when saving.
        if (savePosition) saveCurrentPositionForAuto(appendToLog = true)

        val currentItem = currentPlayer.currentMediaItem
        val rawPositionMs = currentPlayer.currentPosition
        // When a Cast session ends, CastPlayer.currentPosition often reads 0 because it has
        // already disconnected from the receiver. Fall back to the last position the receiver
        // reported so local playback resumes where casting left off (instead of 0:00).
        val positionMs = if (currentPlayer is CastPlayer && rawPositionMs <= 0L && lastKnownCastPositionMs > 0L) {
            lastKnownCastPositionMs
        } else {
            rawPositionMs
        }
        val playWhenReady = currentPlayer.playWhenReady
        val playbackState = currentPlayer.playbackState
        val shouldPlay = playWhenReady && playbackState != Player.STATE_ENDED

        currentPlayer.stop()
        session.player = newPlayer

        if (newPlayer is CastPlayer) {
            // Going to Cast. Remember the local item so we can restore it when casting ends.
            // Seed the last-known cast position with where we're starting so an immediate
            // disconnect still restores the right spot. Bypass Media3 CastPlayer.setMediaItem
            // (which calls queueLoad → INVALID_PARAMS) — send a plain LOAD directly via
            // RemoteMediaClient to the LAN HTTP server.
            lastKnownCastPositionMs = positionMs
            val sourceLocalItem = currentItem?.also { lastLocalMediaItem = it } ?: lastLocalMediaItem
            if (sourceLocalItem != null) {
                val castItem = buildCastMediaItem(sourceLocalItem) ?: sourceLocalItem
                sendDirectCastLoad(castItem, positionMs, autoplay = shouldPlay)
            }
        } else {
            // Returning to local. CastPlayer's currentMediaItem carries the http:// LAN URL
            // with an unmappable mediaId, so rebuild from the remembered pre-cast local item.
            // Never hand ExoPlayer an http:// URI — cleartext is blocked and we want the file.
            val sourceItem = lastLocalMediaItem ?: currentItem
            val localItem = sourceItem?.let { buildLocalMediaItem(it) }
            if (localItem != null) {
                newPlayer.setMediaItem(localItem, positionMs)
                newPlayer.prepare()
                newPlayer.playWhenReady = shouldPlay
            } else {
                Log.w(TAG, "switchToPlayer: no local item to restore after Cast; ExoPlayer left idle")
            }
        }
    }

    /**
     * Rebuilds a MediaItem pointing at the on-device LAN HTTP server
     * (`http://<phone-ip>:<port>/<token>/<filename>`) so the Cast receiver streams the
     * downloaded audiobook directly from the phone. Returns null if the local cast server
     * isn't running, the mediaId is unrecognised, or the file isn't downloaded.
     */
    private fun buildCastMediaItem(original: MediaItem): MediaItem? {
        val ip = localCastIp
        val port = localCastPort
        val token = localCastPathToken
        if (ip == null || port == null || token == null) {
            Log.w(TAG, "buildCastMediaItem: local cast server not running — cannot build URL")
            return null
        }

        val mediaId = original.mediaId
        val filename = when {
            mediaId.startsWith("pair_") -> {
                val pairId = mediaId.removePrefix("pair_").toIntOrNull() ?: return null
                runBlocking { repository.getPairById(pairId) }?.audiobookFilename
            }
            mediaId.startsWith("audiobook_") -> {
                val audiobookId = mediaId.removePrefix("audiobook_").toIntOrNull() ?: return null
                runBlocking { repository.getAudiobookById(audiobookId) }?.filename
            }
            else -> null
        } ?: run {
            Log.w(TAG, "buildCastMediaItem: no filename for mediaId=$mediaId")
            return null
        }

        // Verify the file exists locally — Cast streams from the phone, so if the file isn't
        // downloaded the receiver would 404 and idle.
        if (!File(File(filesDir, "audiobooks"), filename).isFile) {
            Log.w(TAG, "buildCastMediaItem: local file missing for '$filename'")
            return null
        }

        val mimeType = when (filename.substringAfterLast('.', "").lowercase()) {
            "mp3"        -> MimeTypes.AUDIO_MPEG
            "m4b", "m4a" -> MimeTypes.AUDIO_MP4
            "flac"       -> MimeTypes.AUDIO_FLAC
            "ogg"        -> MimeTypes.AUDIO_OGG
            "aac"        -> MimeTypes.AUDIO_AAC
            "wav"        -> "audio/wav"
            else         -> MimeTypes.AUDIO_MPEG
        }

        // URL-encode the filename so spaces and other special chars survive the URL parse on
        // the receiver. Don't encode the path token (it's already hex).
        val encodedFilename = Uri.encode(filename)
        val streamUrl = "http://$ip:$port/$token/$encodedFilename"

        // Artwork is intentionally not set — the Cast receiver runs in a Chrome browser context
        // and can't fetch a content:// URI, and we don't have a public-LAN cover image to
        // substitute. The Default Media Receiver tolerates missing artwork (just shows its
        // default icon).
        Log.d(TAG, "buildCastMediaItem: url=$streamUrl mimeType=$mimeType")
        return original.buildUpon()
            .setUri(streamUrl)
            .setMimeType(mimeType)
            .setMediaMetadata(
                original.mediaMetadata.buildUpon()
                    .setArtworkUri(null)
                    .build()
            )
            .build()
    }

    /**
     * Sends a LOAD command directly to the Cast receiver via RemoteMediaClient, bypassing
     * Media3 CastPlayer's queueLoad path. Diagnostic for INVALID_PARAMS rejections.
     *
     * The result callback logs the receiver's exact status code so we can distinguish between:
     *   - Receiver rejected the LOAD format (statusCode != SUCCESS)
     *   - Receiver accepted but couldn't fetch the URL (LOAD_FAILED)
     *   - Receiver played successfully (SUCCESS)
     */
    private fun sendDirectCastLoad(castItem: MediaItem, positionMs: Long, autoplay: Boolean) {
        try {
            val castContext = CastContext.getSharedInstance()
            val session = castContext?.sessionManager?.currentCastSession
            val client = session?.remoteMediaClient
            if (client == null) {
                Log.w(TAG, "sendDirectCastLoad: no RemoteMediaClient available")
                return
            }

            val localConfig = castItem.localConfiguration
            if (localConfig?.uri == null || localConfig.mimeType == null) {
                Log.w(TAG, "sendDirectCastLoad: cast item missing uri/mimeType")
                return
            }
            val url = localConfig.uri.toString()
            val mimeType = localConfig.mimeType!!

            val castMetadata = com.google.android.gms.cast.MediaMetadata(
                com.google.android.gms.cast.MediaMetadata.MEDIA_TYPE_MUSIC_TRACK
            )
            castItem.mediaMetadata.title?.let {
                castMetadata.putString(
                    com.google.android.gms.cast.MediaMetadata.KEY_TITLE,
                    it.toString()
                )
            }
            castItem.mediaMetadata.artist?.let {
                castMetadata.putString(
                    com.google.android.gms.cast.MediaMetadata.KEY_ARTIST,
                    it.toString()
                )
            }
            castItem.mediaMetadata.artworkUri?.let {
                castMetadata.addImage(com.google.android.gms.common.images.WebImage(it))
            }

            val mediaInfo = com.google.android.gms.cast.MediaInfo.Builder(url)
                .setStreamType(com.google.android.gms.cast.MediaInfo.STREAM_TYPE_BUFFERED)
                .setContentType(mimeType)
                .setContentUrl(url)
                .setMetadata(castMetadata)
                .build()

            val request = com.google.android.gms.cast.MediaLoadRequestData.Builder()
                .setMediaInfo(mediaInfo)
                .setAutoplay(autoplay)
                .setCurrentTime(positionMs)
                .build()

            Log.d(TAG, "sendDirectCastLoad: LOAD positionMs=$positionMs autoplay=$autoplay")
            val task = client.load(request)
            task.setResultCallback { result ->
                val status = result.status
                if (status.isSuccess) {
                    Log.d(TAG, "sendDirectCastLoad: receiver acked LOAD")
                } else {
                    Log.w(
                        TAG,
                        "sendDirectCastLoad: LOAD rejected code=${status.statusCode} message=${status.statusMessage}"
                    )
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "sendDirectCastLoad: exception while dispatching LOAD", e)
        }
    }

    /**
     * Rebuilds a MediaItem with a local file:// URI for ExoPlayer.
     * Used when switching back from Cast to local playback.
     * Returns null if the mediaId is unrecognised or the file is missing.
     */
    private fun buildLocalMediaItem(original: MediaItem): MediaItem? {
        val mediaId = original.mediaId
        val audioFile = when {
            mediaId.startsWith("pair_") -> {
                val pairId = mediaId.removePrefix("pair_").toIntOrNull() ?: return null
                val pair = runBlocking { repository.getPairById(pairId) } ?: return null
                File(filesDir, "audiobooks/${pair.audiobookFilename}")
            }
            mediaId.startsWith("audiobook_") -> {
                val audiobookId = mediaId.removePrefix("audiobook_").toIntOrNull() ?: return null
                val audio = runBlocking { repository.getAudiobookById(audiobookId) } ?: return null
                File(filesDir, "audiobooks/${audio.filename}")
            }
            else -> return null
        }
        if (!audioFile.exists()) return null
        return original.buildUpon().setUri(Uri.fromFile(audioFile)).build()
    }

    // =========================================================
    // Position saving
    // =========================================================

    private fun saveLastPosition(positionMs: Long, mediaId: String?) {
        sharedPrefs.edit()
            .putLong(PREF_LAST_POSITION, positionMs)
            .putLong(PREF_LAST_SAVED_AT, System.currentTimeMillis())
            .also { editor ->
                if (mediaId != null) editor.putString(PREF_LAST_MEDIA_ID, mediaId)
            }
            .apply()
    }

    /**
     * Saves the current playback position to the local database.
     * Called every AUTO_SAVE_INTERVAL_MS while playing and on pause/stop boundaries.
     * This is the Auto counterpart to the phone app's PlayerViewModel save loop.
     *
     * @param appendToLog false for 5-second heartbeat saves (position-only, no
     *   history entry). True on pause / stop / 30-min-tick / cast transitions —
     *   those produce a BookmarkLog row and reset the continuous-playback timer.
     */
    private fun saveCurrentPositionForAuto(appendToLog: Boolean = false) {
        val player = mediaLibrarySession?.player ?: return
        val mediaId = player.currentMediaItem?.mediaId ?: return
        val posMs = player.currentPosition.toInt()
        if (posMs <= 0) return

        if (appendToLog) lastAutoLogTimeMs = System.currentTimeMillis()

        serviceScope.launch {
            try {
                when {
                    mediaId.startsWith("pair_") -> {
                        val pairId = mediaId.removePrefix("pair_").toIntOrNull() ?: return@launch
                        repository.savePlaybackPosition(
                            pairId = pairId,
                            audioPositionMs = posMs,
                            appendToLog = appendToLog,
                        )
                    }
                    mediaId.startsWith("audiobook_") -> {
                        // Standalone audiobooks: no pair → no bookmark_log entry to
                        // worry about. updateProgress only writes UserProgress.
                        val audiobookId = mediaId.removePrefix("audiobook_").toIntOrNull() ?: return@launch
                        repository.savePlaybackPositionStandalone(
                            audiobookId = audiobookId,
                            audioPositionMs = posMs,
                        )
                    }
                }
            } catch (e: Exception) {
                Log.w(TAG, "Failed to save Auto position", e)
            }
        }
    }

    private fun startAutoPositionSave() {
        autoPositionSaveJob?.cancel()
        autoPositionSaveJob = serviceScope.launch {
            while (true) {
                delay(AUTO_SAVE_INTERVAL_MS)
                // Heartbeat: keep bookmark position fresh, no history entry.
                saveCurrentPositionForAuto(appendToLog = false)
                // 30-min continuous-playback tick: write a single history entry
                // and reset the timer. Only reached while isPlaying (the loop is
                // torn down by stopAutoPositionSave on pause), so pauses freeze
                // the clock automatically.
                if (System.currentTimeMillis() - lastAutoLogTimeMs >= AUTO_LOG_INTERVAL_MS) {
                    saveCurrentPositionForAuto(appendToLog = true)
                }
            }
        }
    }

    private fun stopAutoPositionSave() {
        autoPositionSaveJob?.cancel()
        autoPositionSaveJob = null
    }

    // =========================================================
    // Sleep timer
    // =========================================================

    private fun handleSleepTimer(minutes: Int) {
        sleepTimerJob?.cancel()
        if (minutes <= 0) return

        sleepTimerJob = serviceScope.launch {
            val totalMs = minutes * 60 * 1000L
            val waitMs = maxOf(0L, totalMs - 30_000L)
            delay(waitMs)

            val fadeSteps = 30
            val fadeDuration = minOf(30_000L, totalMs)
            val fadeInterval = fadeDuration / fadeSteps
            val player = mediaLibrarySession?.player ?: return@launch

            for (i in fadeSteps downTo 0) {
                if (!player.isPlaying) return@launch
                player.volume = 1.0f * i / fadeSteps
                delay(fadeInterval)
            }
            player.pause()
            player.volume = 1.0f
        }
    }

    // =========================================================
    // Chapter extraction
    // =========================================================

    @OptIn(UnstableApi::class)
    private fun getChaptersBundle(player: Player): Bundle {
        val result = Bundle()
        val titles = mutableListOf<String>()
        val startTimesMs = mutableListOf<Long>()

        try {
            val timeline = player.currentTimeline
            if (timeline.windowCount > 1) {
                val window = androidx.media3.common.Timeline.Window()
                for (i in 0 until timeline.windowCount) {
                    timeline.getWindow(i, window)
                    val title = window.mediaItem.mediaMetadata.title?.toString()
                        ?: "Chapter ${i + 1}"
                    titles.add(title)
                    startTimesMs.add(window.defaultPositionMs)
                }
            } else if (timeline.windowCount == 1) {
                val uri = player.currentMediaItem?.localConfiguration?.uri
                if (uri != null) {
                    val retriever = android.media.MediaMetadataRetriever()
                    try {
                        retriever.setDataSource(this, uri)
                    } catch (_: Exception) {
                    } finally {
                        retriever.release()
                    }
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "Error extracting chapters", e)
        }

        result.putInt("count", titles.size)
        result.putStringArray("titles", titles.toTypedArray())
        result.putLongArray("startTimesMs", startTimesMs.toLongArray())
        return result
    }

    // =========================================================
    // MediaLibrarySession browse callback
    // =========================================================

    private inner class BrowseCallback : MediaLibrarySession.Callback {

        // --- Connection / custom commands (phone app) ---

        override fun onConnect(
            session: MediaSession,
            controller: MediaSession.ControllerInfo
        ): MediaSession.ConnectionResult {
            diagnosticLogger.i(LogChannel.AUTO, TAG, "onConnect pkg=${controller.packageName} uid=${controller.uid}")
            val sessionCommands = MediaSession.ConnectionResult.DEFAULT_SESSION_AND_LIBRARY_COMMANDS.buildUpon()
                .add(SessionCommand(CMD_SET_SPEED, Bundle.EMPTY))
                .add(SessionCommand(CMD_SET_SLEEP_TIMER, Bundle.EMPTY))
                .add(SessionCommand(CMD_GET_SPEED, Bundle.EMPTY))
                .add(SessionCommand(CMD_GET_CHAPTERS, Bundle.EMPTY))
                .build()
            // AudiobookPlayer (ForwardingPlayer) already removes SEEK_TO_PREVIOUS/NEXT globally,
            // so no per-controller command restriction is needed here.
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
                    sharedPrefs.edit().putFloat(PREF_SPEED, speed).apply()
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

        override fun onDisconnected(
            session: MediaSession,
            controller: MediaSession.ControllerInfo
        ) {
            diagnosticLogger.i(LogChannel.AUTO, TAG, "onDisconnected pkg=${controller.packageName}")
            // Save position when any client disconnects (covers Android Auto disconnect on car shutoff)
            saveCurrentPositionForAuto()
        }

        // --- Browse tree ---

        override fun onGetLibraryRoot(
            session: MediaLibrarySession,
            browser: MediaSession.ControllerInfo,
            params: LibraryParams?
        ): ListenableFuture<LibraryResult<MediaItem>> {
            diagnosticLogger.i(LogChannel.AUTO, TAG, "onGetLibraryRoot pkg=${browser.packageName} isRecent=${params?.isRecent}")
            // Return the same root for all clients, including Android Auto (which always
            // sends isRecent=true). Playback resumption is handled by onPlaybackResumption.
            val root = MediaItem.Builder()
                .setMediaId("[root]")
                .setMediaMetadata(
                    MediaMetadata.Builder()
                        .setTitle("BookSync")
                        .setIsBrowsable(true)
                        .setIsPlayable(false)
                        .setMediaType(MediaMetadata.MEDIA_TYPE_FOLDER_MIXED)
                        .build()
                )
                .build()
            return Futures.immediateFuture(LibraryResult.ofItem(root, params))
        }

        override fun onGetChildren(
            session: MediaLibrarySession,
            browser: MediaSession.ControllerInfo,
            parentId: String,
            page: Int,
            pageSize: Int,
            params: LibraryParams?
        ): ListenableFuture<LibraryResult<ImmutableList<MediaItem>>> {
            diagnosticLogger.i(LogChannel.AUTO, TAG, "onGetChildren parentId=$parentId page=$page pkg=${browser.packageName}")
            return when (parentId) {
                "[root]" -> Futures.immediateFuture(
                    LibraryResult.ofItemList(buildRootTabs(), params)
                )
                "continue_listening" -> buildContinueListeningItems(params)
                "library" -> buildLibraryItems(params)
                else -> Futures.immediateFuture(
                    LibraryResult.ofItemList(ImmutableList.of(), params)
                )
            }
        }

        override fun onGetItem(
            session: MediaLibrarySession,
            browser: MediaSession.ControllerInfo,
            mediaId: String
        ): ListenableFuture<LibraryResult<MediaItem>> {
            val future = SettableFuture.create<LibraryResult<MediaItem>>()
            serviceScope.launch(Dispatchers.IO) {
                val item = resolveMediaItem(mediaId)
                future.set(
                    if (item != null) LibraryResult.ofItem(item, null)
                    else LibraryResult.ofError(LibraryResult.RESULT_ERROR_BAD_VALUE)
                )
            }
            return future
        }

        override fun onPlaybackResumption(
            mediaSession: MediaSession,
            controller: MediaSession.ControllerInfo
        ): ListenableFuture<MediaSession.MediaItemsWithStartPosition> {
            // Cast path: bypass Media3's CastPlayer.queueLoad() entirely. queueLoad sends a
            // QUEUE_LOAD message which the Default Media Receiver consistently rejects with
            // INVALID_PARAMS (2001) even for properly-formed single-item queues. Instead, send
            // a plain LOAD via RemoteMediaClient.load() directly, with the result callback
            // logging the exact receiver status code so we can see what (if anything) is wrong.
            if (mediaLibrarySession?.player is CastPlayer) {
                val mediaId = sharedPrefs.getString(PREF_LAST_MEDIA_ID, null)
                    ?: return Futures.immediateFailedFuture(
                        UnsupportedOperationException("Cast resumption: no saved media id")
                    )
                serviceScope.launch(Dispatchers.IO) {
                    val placeholder = MediaItem.Builder().setMediaId(mediaId).build()
                    val castItem = buildCastMediaItem(placeholder)
                    if (castItem == null) {
                        Log.w(TAG, "Cast resumption: cannot build cast item for $mediaId")
                        return@launch
                    }
                    val positionMs = when {
                        mediaId.startsWith("pair_") -> {
                            val pairId = mediaId.removePrefix("pair_").toIntOrNull()
                            pairId?.let { refreshPositionBeforeResume(it) }
                            pairId?.let { repository.getBookmark(it)?.audioPositionMs?.toLong() }
                                ?: sharedPrefs.getLong(PREF_LAST_POSITION, 0L)
                        }
                        mediaId.startsWith("audiobook_") -> {
                            val audiobookId = mediaId.removePrefix("audiobook_").toIntOrNull()
                            audiobookId?.let { repository.getProgressOnce("audiobook", it)?.audioPositionMs?.toLong() }
                                ?: sharedPrefs.getLong(PREF_LAST_POSITION, 0L)
                        }
                        else -> sharedPrefs.getLong(PREF_LAST_POSITION, 0L)
                    }
                    // CastContext.getSharedInstance() requires the main thread.
                    withContext(Dispatchers.Main) {
                        sendDirectCastLoad(castItem, positionMs, autoplay = true)
                    }
                }
                return Futures.immediateFailedFuture(
                    UnsupportedOperationException("Cast LOAD dispatched directly via RemoteMediaClient")
                )
            }
            // For local playback, auto-resumption is disabled intentionally. When this future
            // fails, Android Auto falls back to the browse UI where "Continue Listening" shows
            // the book at the correct DB-backed position. The user presses play to start — no
            // auto-play on connect. This also eliminates the stale-SharedPrefs position bug
            // (PREF_LAST_POSITION was only written at service destroy time, so could be 0 if
            // the service was SIGKILL'd mid-session; the DB bookmark is always current).
            return Futures.immediateFailedFuture(
                UnsupportedOperationException("Auto-resumption disabled — user initiates playback")
            )
        }

        override fun onSetMediaItems(
            mediaSession: MediaSession,
            controller: MediaSession.ControllerInfo,
            mediaItems: List<MediaItem>,
            startIndex: Int,
            startPositionMs: Long
        ): ListenableFuture<MediaSession.MediaItemsWithStartPosition> {
            diagnosticLogger.i(LogChannel.AUTO, TAG, "onSetMediaItems count=${mediaItems.size} startIndex=$startIndex startPos=${startPositionMs}ms pkg=${controller.packageName} ids=${mediaItems.map { it.mediaId }}")

            // When Cast is active, returning items here triggers CastPlayer.setMediaItems → a LOAD,
            // AND handleMediaControllerPlayRequest still falls through to onPlaybackResumption
            // (because CastPlayer.getMediaItemCount() is async — stays 0 until the receiver
            // confirms). That fires a SECOND LOAD ~20ms later, and the Default Media Receiver
            // rejects the burst with INVALID_PARAMS (2001). Defer to onPlaybackResumption so
            // only one LOAD is issued.
            if (mediaLibrarySession?.player is CastPlayer) {
                Log.d(TAG, "onSetMediaItems: Cast active — deferring LOAD to onPlaybackResumption")
                return Futures.immediateFailedFuture(
                    UnsupportedOperationException("Cast path: LOAD owned by onPlaybackResumption")
                )
            }

            val future = SettableFuture.create<MediaSession.MediaItemsWithStartPosition>()
            serviceScope.launch(Dispatchers.IO) {
                // Legacy Android Auto path (onPlayFromMediaId) sends MediaItems with only
                // mediaId set and no URI. Resolve them to full items with file:// URIs.
                val resolvedItems = mediaItems.map { item ->
                    if (item.localConfiguration?.uri == null) {
                        resolveMediaItem(item.mediaId) ?: item
                    } else {
                        item
                    }
                }
                // Google Assistant / Android Auto pass startIndex = C.INDEX_UNSET (-1) when
                // they want the player to use its default. getOrNull(-1) returns null, which
                // would make us lose the bookmarked position embedded in the resolved item's
                // extras and start from 0. Normalize to 0 (first item) for both lookup and
                // the returned MediaItemsWithStartPosition.
                val effectiveStartIndex =
                    if (startIndex < 0 || startIndex >= resolvedItems.size) 0 else startIndex
                val item = resolvedItems.getOrNull(effectiveStartIndex)
                val resumeMs = item?.mediaMetadata?.extras?.getLong("resumePositionMs", 0L) ?: 0L
                val resolvedPosition = if (startPositionMs != C.TIME_UNSET && startPositionMs > 0) {
                    startPositionMs
                } else {
                    resumeMs
                }
                diagnosticLogger.i(LogChannel.AUTO, TAG, "onSetMediaItems resolved effectiveStartIndex=$effectiveStartIndex resumeMs=$resumeMs resolvedPosition=$resolvedPosition mediaId=${item?.mediaId}")
                future.set(
                    MediaSession.MediaItemsWithStartPosition(resolvedItems, effectiveStartIndex, resolvedPosition)
                )
            }
            return future
        }
    }

    // =========================================================
    // Browse tree helpers
    // =========================================================

    private fun buildRootTabs(): ImmutableList<MediaItem> {
        return ImmutableList.of(
            MediaItem.Builder()
                .setMediaId("continue_listening")
                .setMediaMetadata(
                    MediaMetadata.Builder()
                        .setTitle("Continue Listening")
                        .setIsBrowsable(true)
                        .setIsPlayable(false)
                        .setMediaType(MediaMetadata.MEDIA_TYPE_FOLDER_MIXED)
                        .build()
                )
                .build(),
            MediaItem.Builder()
                .setMediaId("library")
                .setMediaMetadata(
                    MediaMetadata.Builder()
                        .setTitle("Library")
                        .setIsBrowsable(true)
                        .setIsPlayable(false)
                        .setMediaType(MediaMetadata.MEDIA_TYPE_FOLDER_MIXED)
                        .build()
                )
                .build()
        )
    }

    private fun buildContinueListeningItems(
        params: LibraryParams?
    ): ListenableFuture<LibraryResult<ImmutableList<MediaItem>>> {
        val future = SettableFuture.create<LibraryResult<ImmutableList<MediaItem>>>()
        serviceScope.launch(Dispatchers.IO) {
            try {
                val recentPairs = repository.getRecentlyPlayedPairsFlow().first()
                val recentStandalone = repository.getRecentlyPlayedStandaloneAudiobooksFlow().first()

                val items = mutableListOf<MediaItem>()

                for (pair in recentPairs) {
                    val bookmark = repository.getBookmark(pair.id)
                    val resumeMs = bookmark?.audioPositionMs?.toLong() ?: 0L
                    val coverUri = coverArtHelper.getCoverUri(pair.audiobookId, pair.audiobookFilename)
                    coverUri?.let { coverArtHelper.grantAutoReadPermission(it) }
                    items.add(buildPairMediaItem(pair, resumeMs, coverUri))
                }

                for (audio in recentStandalone) {
                    val progress = repository.getProgressOnce("audiobook", audio.id)
                    val resumeMs = progress?.audioPositionMs?.toLong() ?: 0L
                    val coverUri = coverArtHelper.getCoverUri(audio.id, audio.filename)
                    coverUri?.let { coverArtHelper.grantAutoReadPermission(it) }
                    items.add(buildAudiobookMediaItem(audio, resumeMs, coverUri))
                }

                future.set(LibraryResult.ofItemList(ImmutableList.copyOf(items), params))
            } catch (e: Exception) {
                Log.e(TAG, "Error building Continue Listening", e)
                future.set(LibraryResult.ofItemList(ImmutableList.of(), params))
            }
        }
        return future
    }

    private fun buildLibraryItems(
        params: LibraryParams?
    ): ListenableFuture<LibraryResult<ImmutableList<MediaItem>>> {
        val future = SettableFuture.create<LibraryResult<ImmutableList<MediaItem>>>()
        serviceScope.launch(Dispatchers.IO) {
            try {
                val downloadedPairs = repository.getDownloadedPairsFlow().first()
                    .filter { it.audiobookDownloaded }
                val downloadedStandalone = repository.getDownloadedAudiobooksAlphabeticalFlow().first()

                // Avoid showing the same audiobook twice if it's already represented by a pair
                val pairedAudiobookIds = downloadedPairs.map { it.audiobookId }.toSet()

                val items = mutableListOf<MediaItem>()

                for (pair in downloadedPairs) {
                    val bookmark = repository.getBookmark(pair.id)
                    val resumeMs = bookmark?.audioPositionMs?.toLong() ?: 0L
                    val coverUri = coverArtHelper.getCoverUri(pair.audiobookId, pair.audiobookFilename)
                    coverUri?.let { coverArtHelper.grantAutoReadPermission(it) }
                    items.add(buildPairMediaItem(pair, resumeMs, coverUri))
                }

                for (audio in downloadedStandalone) {
                    if (audio.id in pairedAudiobookIds) continue
                    val progress = repository.getProgressOnce("audiobook", audio.id)
                    val resumeMs = progress?.audioPositionMs?.toLong() ?: 0L
                    val coverUri = coverArtHelper.getCoverUri(audio.id, audio.filename)
                    coverUri?.let { coverArtHelper.grantAutoReadPermission(it) }
                    items.add(buildAudiobookMediaItem(audio, resumeMs, coverUri))
                }

                // Final alphabetical sort across pairs and standalone
                items.sortBy { it.mediaMetadata.title?.toString()?.lowercase() ?: "" }

                future.set(LibraryResult.ofItemList(ImmutableList.copyOf(items), params))
            } catch (e: Exception) {
                Log.e(TAG, "Error building Library", e)
                future.set(LibraryResult.ofItemList(ImmutableList.of(), params))
            }
        }
        return future
    }

    /**
     * Pull the server's position before resuming, bounded so an unreachable
     * server can't stall playback.
     *
     * Only the paths that decide where audio *starts* do this. The browse-tree
     * builders deliberately don't: they render a list and would otherwise fire
     * one request per row on every browse.
     */
    private suspend fun refreshPositionBeforeResume(pairId: Int) {
        withTimeoutOrNull(SERVER_POSITION_TIMEOUT_MS) {
            repository.refreshBookmark(pairId)
        } ?: Log.w(TAG, "resume: server position unavailable in time — using local cache")
    }

    private suspend fun resolveMediaItem(mediaId: String): MediaItem? {
        return when {
            mediaId.startsWith("pair_") -> {
                val pairId = mediaId.removePrefix("pair_").toIntOrNull() ?: return null
                val pair = repository.getPairById(pairId) ?: return null
                // This is the playback-start path — what it returns is where
                // audio actually begins — so pull the server's position first.
                // Reading only the local cache meant a position set on another
                // device was never seen here. Bounded, so an unreachable server
                // falls back to the cache instead of stalling playback.
                refreshPositionBeforeResume(pairId)
                val bookmark = repository.getBookmark(pairId)
                val resumeMs = bookmark?.audioPositionMs?.toLong() ?: 0L
                val coverUri = coverArtHelper.getCoverUri(pair.audiobookId, pair.audiobookFilename)
                buildPairMediaItem(pair, resumeMs, coverUri)
            }
            mediaId.startsWith("audiobook_") -> {
                val audiobookId = mediaId.removePrefix("audiobook_").toIntOrNull() ?: return null
                val audio = repository.getAudiobookById(audiobookId) ?: return null
                val progress = repository.getProgressOnce("audiobook", audiobookId)
                val resumeMs = progress?.audioPositionMs?.toLong() ?: 0L
                val coverUri = coverArtHelper.getCoverUri(audiobookId, audio.filename)
                buildAudiobookMediaItem(audio, resumeMs, coverUri)
            }
            else -> null
        }
    }

    private fun buildPairMediaItem(
        pair: BookPairEntity,
        resumePositionMs: Long,
        coverUri: Uri?
    ): MediaItem {
        val extras = Bundle().apply {
            putLong("resumePositionMs", resumePositionMs)
            putLong("durationMs", (pair.audiobookDurationSeconds ?: 0) * 1000L)
            putString("sourceType", "pair")
            putInt("pairId", pair.id)
        }
        return MediaItem.Builder()
            .setMediaId("pair_${pair.id}")
            .setUri(Uri.fromFile(File(filesDir, "audiobooks/${pair.audiobookFilename}")))
            .setMediaMetadata(
                MediaMetadata.Builder()
                    .setTitle(pair.audiobookTitle)
                    .setArtist(pair.audiobookAuthor)
                    .setArtworkUri(coverUri)
                    .setMediaType(MediaMetadata.MEDIA_TYPE_AUDIO_BOOK)
                    .setIsBrowsable(false)
                    .setIsPlayable(true)
                    .setExtras(extras)
                    .build()
            )
            .build()
    }

    private fun buildAudiobookMediaItem(
        audio: AudioBookEntity,
        resumePositionMs: Long,
        coverUri: Uri?
    ): MediaItem {
        val extras = Bundle().apply {
            putLong("resumePositionMs", resumePositionMs)
            putLong("durationMs", (audio.durationSeconds ?: 0) * 1000L)
            putString("sourceType", "standalone")
            putInt("audiobookId", audio.id)
        }
        return MediaItem.Builder()
            .setMediaId("audiobook_${audio.id}")
            .setUri(Uri.fromFile(File(filesDir, "audiobooks/${audio.filename}")))
            .setMediaMetadata(
                MediaMetadata.Builder()
                    .setTitle(audio.title)
                    .setArtist(audio.author)
                    .setArtworkUri(coverUri)
                    .setMediaType(MediaMetadata.MEDIA_TYPE_AUDIO_BOOK)
                    .setIsBrowsable(false)
                    .setIsPlayable(true)
                    .setExtras(extras)
                    .build()
            )
            .build()
    }
}
