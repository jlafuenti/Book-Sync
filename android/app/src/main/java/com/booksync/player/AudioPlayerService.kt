package com.booksync.player

import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.net.Uri
import android.os.Bundle
import android.util.Log
import androidx.annotation.OptIn
import androidx.media3.cast.CastPlayer
import androidx.media3.cast.SessionAvailabilityListener
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.ForwardingPlayer
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
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
import java.io.File
import javax.inject.Inject

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
    }

    @Inject lateinit var repository: BookSyncRepository
    @Inject lateinit var coverArtHelper: CoverArtHelper
    @Inject lateinit var tokenManager: TokenManager
    @Inject lateinit var diagnosticLogger: com.booksync.diagnostics.DiagnosticLogger

    private var mediaLibrarySession: MediaLibrarySession? = null
    private var castPlayer: CastPlayer? = null
    private var exoPlayer: Player? = null
    private var sleepTimerJob: Job? = null
    private var autoPositionSaveJob: Job? = null
    private val serviceScope = CoroutineScope(Dispatchers.Main + SupervisorJob())
    private lateinit var sharedPrefs: SharedPreferences

    private val castSessionListener = object : SessionManagerListener<CastSession> {
        override fun onSessionStarted(session: CastSession, sessionId: String) {
            castPlayer?.let { switchToPlayer(it, savePosition = true) }
        }
        override fun onSessionResumed(session: CastSession, wasSuspended: Boolean) {
            castPlayer?.let { switchToPlayer(it, savePosition = false) }
        }
        override fun onSessionEnded(session: CastSession, error: Int) {
            exoPlayer?.let { switchToPlayer(it, savePosition = true) }
        }
        override fun onSessionSuspended(session: CastSession, reason: Int) {
            exoPlayer?.let { switchToPlayer(it, savePosition = true) }
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
                    saveCurrentPositionForAuto()
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
            val cast = CastPlayer(castContext)
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
            if (!player.playWhenReady) {
                stopSelf()
            }
        }
    }

    override fun onDestroy() {
        sleepTimerJob?.cancel()
        stopAutoPositionSave()
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

        if (savePosition) saveCurrentPositionForAuto()

        val currentItem = currentPlayer.currentMediaItem
        val positionMs = currentPlayer.currentPosition
        val playWhenReady = currentPlayer.playWhenReady
        val playbackState = currentPlayer.playbackState

        currentPlayer.stop()
        session.player = newPlayer

        if (currentItem != null) {
            // Rebuild the item URI to match the target player:
            //   CastPlayer → server HTTPS URL (Chromecast can't access local files)
            //   ExoPlayer  → local file:// URI
            val itemForNewPlayer = if (newPlayer is CastPlayer) {
                buildCastMediaItem(currentItem) ?: currentItem
            } else {
                buildLocalMediaItem(currentItem) ?: currentItem
            }
            newPlayer.setMediaItem(itemForNewPlayer, positionMs)
            newPlayer.prepare()
            newPlayer.playWhenReady = playWhenReady && playbackState != Player.STATE_ENDED
        }
    }

    /**
     * Rebuilds a MediaItem with an HTTPS server URL suitable for the Cast receiver.
     * The JWT token is appended as a query parameter so the Chromecast can authenticate.
     * Returns null if the mediaId is unrecognised.
     */
    private fun buildCastMediaItem(original: MediaItem): MediaItem? {
        val mediaId = original.mediaId
        val token = runBlocking { tokenManager.getAccessToken().firstOrNull() } ?: ""
        val baseUrl = com.booksync.BuildConfig.SERVER_BASE_URL.trimEnd('/')

        val streamUrl = when {
            mediaId.startsWith("pair_") -> {
                val pairId = mediaId.removePrefix("pair_").toIntOrNull() ?: return null
                val audiobookId = runBlocking { repository.getPairById(pairId)?.audiobookId }
                    ?: return null
                "$baseUrl/api/files/audiobook/$audiobookId?token=$token"
            }
            mediaId.startsWith("audiobook_") -> {
                val audiobookId = mediaId.removePrefix("audiobook_").toIntOrNull() ?: return null
                "$baseUrl/api/files/audiobook/$audiobookId?token=$token"
            }
            else -> return null
        }

        return original.buildUpon().setUri(streamUrl).build()
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
     * Called every AUTO_SAVE_INTERVAL_MS while playing and immediately on pause/disconnect.
     * This is the Auto counterpart to the phone app's PlayerViewModel 5-second save loop.
     */
    private fun saveCurrentPositionForAuto() {
        val player = mediaLibrarySession?.player ?: return
        val mediaId = player.currentMediaItem?.mediaId ?: return
        val posMs = player.currentPosition.toInt()
        if (posMs <= 0) return

        serviceScope.launch {
            try {
                when {
                    mediaId.startsWith("pair_") -> {
                        val pairId = mediaId.removePrefix("pair_").toIntOrNull() ?: return@launch
                        repository.updateBookmark(
                            pairId = pairId,
                            source = "audiobook",
                            audioPositionMs = posMs
                        )
                    }
                    mediaId.startsWith("audiobook_") -> {
                        val audiobookId = mediaId.removePrefix("audiobook_").toIntOrNull() ?: return@launch
                        repository.updateProgress(
                            mediaType = "audiobook",
                            mediaId = audiobookId,
                            audioPositionMs = posMs
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
                saveCurrentPositionForAuto()
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
            val future = SettableFuture.create<MediaSession.MediaItemsWithStartPosition>()
            serviceScope.launch(Dispatchers.IO) {
                val lastMediaId = sharedPrefs.getString(PREF_LAST_MEDIA_ID, null)
                val lastPositionMs = sharedPrefs.getLong(PREF_LAST_POSITION, 0L)
                val item = if (lastMediaId != null) resolveMediaItem(lastMediaId) else null
                if (item != null) {
                    future.set(
                        MediaSession.MediaItemsWithStartPosition(
                            listOf(item),
                            /* startIndex= */ 0,
                            lastPositionMs
                        )
                    )
                } else {
                    future.setException(UnsupportedOperationException("No last played item"))
                }
            }
            return future
        }

        override fun onSetMediaItems(
            mediaSession: MediaSession,
            controller: MediaSession.ControllerInfo,
            mediaItems: List<MediaItem>,
            startIndex: Int,
            startPositionMs: Long
        ): ListenableFuture<MediaSession.MediaItemsWithStartPosition> {
            diagnosticLogger.i(LogChannel.AUTO, TAG, "onSetMediaItems count=${mediaItems.size} startIndex=$startIndex startPos=${startPositionMs}ms pkg=${controller.packageName} ids=${mediaItems.map { it.mediaId }}")
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
                val item = resolvedItems.getOrNull(startIndex)
                val resumeMs = item?.mediaMetadata?.extras?.getLong("resumePositionMs", 0L) ?: 0L
                val resolvedPosition = if (startPositionMs != C.TIME_UNSET && startPositionMs > 0) {
                    startPositionMs
                } else {
                    resumeMs
                }
                future.set(
                    MediaSession.MediaItemsWithStartPosition(resolvedItems, startIndex, resolvedPosition)
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

    private suspend fun resolveMediaItem(mediaId: String): MediaItem? {
        return when {
            mediaId.startsWith("pair_") -> {
                val pairId = mediaId.removePrefix("pair_").toIntOrNull() ?: return null
                val pair = repository.getPairById(pairId) ?: return null
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
