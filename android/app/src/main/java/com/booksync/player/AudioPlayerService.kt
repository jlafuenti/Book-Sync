package com.booksync.player

import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.net.Uri
import android.os.Bundle
import android.util.Log
import androidx.annotation.OptIn
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
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
import com.booksync.data.repository.BookSyncRepository
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
import kotlinx.coroutines.launch
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

    private var mediaLibrarySession: MediaLibrarySession? = null
    private var sleepTimerJob: Job? = null
    private var autoPositionSaveJob: Job? = null
    private val serviceScope = CoroutineScope(Dispatchers.Main + SupervisorJob())
    private lateinit var sharedPrefs: SharedPreferences

    @OptIn(UnstableApi::class)
    override fun onCreate() {
        super.onCreate()
        sharedPrefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        val initialSpeed = sharedPrefs.getFloat(PREF_SPEED, 1.0f)

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

        // Drive the Auto position-save loop on play/pause events
        player.addListener(object : Player.Listener {
            override fun onIsPlayingChanged(isPlaying: Boolean) {
                if (isPlaying) {
                    startAutoPositionSave()
                } else {
                    stopAutoPositionSave()
                    saveCurrentPositionForAuto()
                }
            }
        })

        mediaLibrarySession = MediaLibrarySession.Builder(this, player, BrowseCallback())
            .setId("AudioPlayerSession")
            .build()
    }

    override fun onGetSession(controllerInfo: MediaSession.ControllerInfo): MediaLibrarySession? {
        return mediaLibrarySession
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
        mediaLibrarySession?.run {
            saveLastPosition(player.currentPosition, player.currentMediaItem?.mediaId)
            player.release()
            release()
        }
        mediaLibrarySession = null
        super.onDestroy()
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
            // Save position when any client disconnects (covers Android Auto disconnect on car shutoff)
            saveCurrentPositionForAuto()
        }

        // --- Browse tree ---

        override fun onGetLibraryRoot(
            session: MediaLibrarySession,
            browser: MediaSession.ControllerInfo,
            params: LibraryParams?
        ): ListenableFuture<LibraryResult<MediaItem>> {
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
            val item = mediaItems.getOrNull(startIndex)
            val resumeMs = item?.mediaMetadata?.extras?.getLong("resumePositionMs", 0L) ?: 0L
            // Honour an explicit seek if provided; otherwise resume from last saved position
            val resolvedPosition = if (startPositionMs != C.TIME_UNSET && startPositionMs > 0) {
                startPositionMs
            } else {
                resumeMs
            }
            return Futures.immediateFuture(
                MediaSession.MediaItemsWithStartPosition(mediaItems, startIndex, resolvedPosition)
            )
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
