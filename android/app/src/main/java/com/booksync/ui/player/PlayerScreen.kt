package com.booksync.ui.player

import android.content.ComponentName
import android.graphics.Bitmap
import android.net.Uri
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.MenuBook
import androidx.compose.material.icons.automirrored.filled.FormatListBulleted
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.material3.TabRowDefaults.tabIndicatorOffset
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.graphics.vector.rememberVectorPainter
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.Player
import androidx.media3.session.MediaController
import androidx.media3.session.SessionCommand
import androidx.media3.session.SessionResult
import androidx.media3.session.SessionToken
import androidx.mediarouter.app.MediaRouteButton
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkInfo
import androidx.work.WorkManager
import androidx.work.workDataOf
import com.booksync.SyncState
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.remote.BookmarkLogResponse
import com.booksync.data.repository.BookSyncRepository
import com.booksync.data.repository.PositionSyncTimeouts.SERVER_POSITION_TIMEOUT_MS
import com.booksync.player.AudioPlayerService
import com.booksync.player.MediaId
import com.booksync.player.MediaSourceSelector
import com.booksync.player.PlaybackOffsets
import com.booksync.player.toUri
import com.booksync.ui.theme.Tandem
import com.booksync.worker.DownloadWorker
import com.google.android.gms.cast.framework.CastButtonFactory
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import android.content.Context
import android.os.Bundle
import android.util.Log
import kotlinx.coroutines.delay
import coil.compose.AsyncImage
import coil.request.ImageRequest
import com.booksync.data.remote.coverImageUrl
import kotlinx.coroutines.withContext
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull

import java.time.format.DateTimeFormatter
import java.util.Locale
import javax.inject.Inject


/**
 * Represents a chapter marker in an M4B audiobook.
 */
data class Chapter(val title: String, val startMs: Long)

/**
 * The media id the phone player gives a standalone audiobook. Must be the id
 * [AudioPlayerService.buildAudiobookMediaItem] builds for the same entity —
 * every service dispatcher (heartbeat, pause flush, completion, Cast) keys on
 * it, and a divergent id makes them all silently no-op (issue #141).
 * Pinned by StandaloneMediaIdParityTest.
 */
internal fun standaloneMediaId(audio: com.booksync.data.local.entity.AudioBookEntity): String =
    MediaId.Audiobook(audio.id).value

/**
 * Audio Player ViewModel.
 * Connects to AudioPlayerService via MediaController for real playback.
 */
@HiltViewModel
class PlayerViewModel @Inject constructor(
    private val repository: BookSyncRepository,
    @param:ApplicationContext private val appContext: Context,
    serverUrlManager: com.booksync.data.remote.ServerUrlManager,
    private val coverArtHelper: com.booksync.auto.CoverArtHelper,
    networkMonitor: com.booksync.data.util.NetworkMonitor,
    castSessionMonitor: com.booksync.cast.CastSessionMonitor,
    savedStateHandle: SavedStateHandle,
) : ViewModel() {

    /** For the cover-art fallback (issue #331); same shape as BookDetailsViewModel. */
    val serverUrl: String = serverUrlManager.currentUrl

    /**
     * The two inputs to [transportEnabled] the screen cannot work out for itself
     * (issue #171): streaming needs a connection, and Cast needs the downloaded
     * file because it serves the phone's copy over the LAN.
     */
    val isOnline: kotlinx.coroutines.flow.StateFlow<Boolean> = networkMonitor.isOnline
    val isCasting: kotlinx.coroutines.flow.StateFlow<Boolean> = castSessionMonitor.isCasting

    private val pairId: Int = savedStateHandle["pairId"] ?: 0
    // audiobookId is set when launched from standalone player route (player/standalone/{audiobookId})
    private val standaloneAudiobookId: Int = savedStateHandle.get<Int>("audiobookId") ?: -1
    val isStandalone: Boolean get() = standaloneAudiobookId > 0

    private val _pair = MutableStateFlow<BookPairEntity?>(null)
    val pair = _pair.asStateFlow()

    private val _standaloneAudio = MutableStateFlow<com.booksync.data.local.entity.AudioBookEntity?>(null)
    val standaloneAudio = _standaloneAudio.asStateFlow()

    private val _isPlaying = MutableStateFlow(false)
    val isPlaying = _isPlaying.asStateFlow()

    private val _positionMs = MutableStateFlow(0L)
    val positionMs = _positionMs.asStateFlow()

    private val _durationMs = MutableStateFlow(0L)
    val durationMs = _durationMs.asStateFlow()

    private val _speed = MutableStateFlow(1.0f)
    val speed = _speed.asStateFlow()

    private val _sleepTimerMinutes = MutableStateFlow(0)
    val sleepTimerMinutes = _sleepTimerMinutes.asStateFlow()

    private val _sleepTimerRemainingMs = MutableStateFlow(0L)
    val sleepTimerRemainingMs = _sleepTimerRemainingMs.asStateFlow()

    private val _coverArtBitmap = MutableStateFlow<Bitmap?>(null)
    val coverArtBitmap = _coverArtBitmap.asStateFlow()

    /**
     * The server's cover path for whatever is playing, or null (issue #331).
     *
     * The player used to render only art embedded in the audio file, so a book
     * whose file had none showed a headphones placeholder while every other
     * screen — and the web UI — displayed the cover the server held for it.
     */
    val serverCoverPath: StateFlow<String?> =
        combine(_pair, _standaloneAudio) { pair, audio ->
            pair?.audiobookCoverPath ?: audio?.coverFilename
        }.stateIn(viewModelScope, SharingStarted.Eagerly, null)

    private val _chapters = MutableStateFlow<List<Chapter>>(emptyList())
    val chapters = _chapters.asStateFlow()

    private val _currentChapterIndex = MutableStateFlow(-1)
    val currentChapterIndex = _currentChapterIndex.asStateFlow()

    private val _history = MutableStateFlow<List<BookmarkLogResponse>>(emptyList())
    val history = _history.asStateFlow()

    private val workManager = WorkManager.getInstance(appContext)

    /** null = not downloading; 0–100 = in progress */
    private val _downloadProgress = MutableStateFlow<Int?>(null)
    val downloadProgress = _downloadProgress.asStateFlow()

    private val _downloadError = MutableStateFlow<String?>(null)
    val downloadError = _downloadError.asStateFlow()

    fun clearDownloadError() { _downloadError.value = null }

    /** Download the standalone audiobook (used from PlayerScreen when isStandalone). */
    fun downloadStandaloneAudiobook() {
        val audio = _standaloneAudio.value ?: return
        val request = DownloadWorker.request(audio.id, "STANDALONE_AUDIOBOOK")
        val workName = "download_standalone_audio_${audio.id}"
        workManager.enqueueUniqueWork(workName, ExistingWorkPolicy.REPLACE, request)
        _downloadProgress.value = 0
        viewModelScope.launch {
            workManager.getWorkInfosForUniqueWorkFlow(workName).collect { infos ->
                val info = infos.firstOrNull() ?: return@collect
                when (info.state) {
                    WorkInfo.State.RUNNING -> {
                        _downloadProgress.value = info.progress.getInt(DownloadWorker.PROGRESS_KEY, 0).coerceIn(0, 100)
                    }
                    WorkInfo.State.SUCCEEDED -> {
                        _downloadProgress.value = null
                        // Refresh the entity from DB so isDownloaded reflects reality
                        repository.getAudiobookById(audio.id)?.let { updated ->
                            _standaloneAudio.value = updated
                            controller?.let { ctrl -> loadStandaloneAudio(updated, ctrl) }
                        }
                    }
                    WorkInfo.State.FAILED -> {
                        _downloadProgress.value = null
                        _downloadError.value = info.outputData.getString(DownloadWorker.ERROR_KEY) ?: "Download failed"
                    }
                    WorkInfo.State.CANCELLED -> _downloadProgress.value = null
                    else -> {}
                }
            }
        }
    }

    fun downloadAudiobook() {
        val pair = _pair.value ?: return
        val request = DownloadWorker.request(pair.id, "AUDIOBOOK")
        workManager.enqueueUniqueWork("download_audio_${pair.id}", ExistingWorkPolicy.REPLACE, request)
        _downloadProgress.value = 0
        viewModelScope.launch {
            workManager.getWorkInfosForUniqueWorkFlow("download_audio_${pair.id}").collect { infos ->
                val info = infos.firstOrNull() ?: return@collect
                when (info.state) {
                    WorkInfo.State.RUNNING -> {
                        val pct = info.progress.getInt(DownloadWorker.PROGRESS_KEY, 0)
                        _downloadProgress.value = pct.coerceIn(0, 100)
                    }
                    WorkInfo.State.SUCCEEDED -> _downloadProgress.value = null
                    WorkInfo.State.FAILED -> {
                        _downloadProgress.value = null
                        _downloadError.value = info.outputData.getString(DownloadWorker.ERROR_KEY) ?: "Download failed"
                    }
                    WorkInfo.State.CANCELLED -> _downloadProgress.value = null
                    else -> {}
                }
            }
        }
    }

    private var controller: MediaController? = null
    private var positionPollingJob: kotlinx.coroutines.Job? = null
    private var coverArtJob: kotlinx.coroutines.Job? = null
    private var savedPositionFromBookmark: Long = 0L
    private var bookmarkLoaded = false
    private var pendingSeekPosition: Long = -1L  // Seek deferred until player is ready
    // No heartbeat or 30-min-tick state here: AudioPlayerService owns the one
    // periodic save and the continuous-playback timer that goes with it (see
    // [ContinuousPlaybackLog]). This screen only saves at boundaries it can
    // recognise and the service cannot — a deliberate pause, or switching to
    // the reader.
    private var chaptersLoaded = false

    companion object {
        val SPEED_OPTIONS = listOf(0.5f, 0.75f, 1.0f, 1.25f, 1.5f, 2.0f)
    }

    init {
        // Check for process-local sentence sync position (bypasses server race condition)
        val pendingSeek = SyncState.pendingAudioSeekMs
        if (pendingSeek > 0) {
            SyncState.pendingAudioSeekMs = -1L
            Log.d("PlayerViewModel", "Found pending sentence sync seek: $pendingSeek ms")
            savedPositionFromBookmark = pendingSeek
            bookmarkLoaded = true
            _positionMs.value = pendingSeek
        }

        if (isStandalone) {
            // Standalone audiobook mode — load by audiobookId, skip pair loading
            viewModelScope.launch {
                val audio = repository.getAudiobookById(standaloneAudiobookId)
                _standaloneAudio.value = audio
                audio?.durationSeconds?.let { _durationMs.value = it * 1000L }

                // Restore before load (issue #142) — mirror of the paired path
                // below. Contract § "Resume paths refresh first": bounded pull
                // of the server position, then the local row. Skipped when a
                // sentence-sync seek already pre-seeded the position above.
                if (!bookmarkLoaded) {
                    withTimeoutOrNull(SERVER_POSITION_TIMEOUT_MS) {
                        repository.refreshProgress("audiobook", standaloneAudiobookId)
                    } ?: Log.w("PlayerViewModel", "server position not available in time — using local cache")

                    repository.getProgressOnce("audiobook", standaloneAudiobookId)
                        ?.audioPositionMs?.let { pos ->
                            savedPositionFromBookmark = pos.toLong()
                            _positionMs.value = pos.toLong()
                            controller?.let { ctrl ->
                                if (ctrl.isConnected && pos > 0) {
                                    if (ctrl.playbackState == Player.STATE_READY) {
                                        restoreSeek(ctrl, pos.toLong())
                                    } else {
                                        pendingSeekPosition = pos.toLong()
                                    }
                                }
                            }
                        }
                    // True even when there is no saved row at all: the restore
                    // has been *attempted*, which is what opens the write gate
                    // in saveBookmark. A genuinely empty record may save.
                    bookmarkLoaded = true
                }

                controller?.let { ctrl ->
                    if (audio != null) loadStandaloneAudio(audio, ctrl)
                }
            }
        } else {

        viewModelScope.launch {
            repository.getPairsFlow().collect { pairs ->
                val found = pairs.find { it.id == pairId }
                _pair.value = found
                found?.audiobookDurationSeconds?.let {
                    _durationMs.value = it * 1000L
                }
                // If controller connected before pair loaded, try loading now
                if (found != null && controller != null) {
                    loadAudio(found, controller!!)
                }
            }
        }
        // Pull the server's position first, then let the local flow below apply
        // it. The refresh used to run *after* the seek, so a position set on
        // another device consistently arrived too late to be used — the same
        // bug the reader had. Bounded so an unreachable server delays the seek
        // by at most a moment before falling back to the local cache.
        viewModelScope.launch {
            withTimeoutOrNull(SERVER_POSITION_TIMEOUT_MS) {
                repository.refreshBookmark(pairId)
            } ?: Log.w("PlayerViewModel", "server position not available in time — using local cache")

            // Same for the sync map: a re-transcription replaces it server-side
            // and the cached points then resolve text to audio timestamps that
            // no longer exist (issue #55). Bounded — the seek must not wait.
            withTimeoutOrNull(SERVER_POSITION_TIMEOUT_MS) {
                repository.ensureSyncMapCached(pairId)
            } ?: Log.w("PlayerViewModel", "sync map not available in time — using local cache")

            repository.getBookmarkFlow(pairId).collect { bm ->
                bm?.audioPositionMs?.let { pos ->
                    Log.d("PlayerViewModel", "Bookmark received: audioPositionMs=$pos, bookmarkLoaded=$bookmarkLoaded, controllerConnected=${controller?.isConnected}")
                    savedPositionFromBookmark = pos.toLong()
                    if (!bookmarkLoaded) {
                        bookmarkLoaded = true
                        _positionMs.value = pos.toLong()
                        // If controller is already ready, seek to saved position
                        controller?.let { ctrl ->
                            if (ctrl.isConnected && pos > 0) {
                                if (ctrl.playbackState == Player.STATE_READY) {
                                    Log.d("PlayerViewModel", "Seeking to bookmark pos=$pos (controller already ready)")
                                    restoreSeek(ctrl, pos.toLong())
                                } else {
                                    Log.d("PlayerViewModel", "Deferring seek to pos=$pos (state=${ctrl.playbackState})")
                                    pendingSeekPosition = pos.toLong()
                                }
                            }
                        }
                    }
                }
            }
        }
        } // end else (non-standalone)

        connectToService()
    }

    private fun connectToService() {
        // Resolving the session token throws when the service can't be found
        // (a broken install; also the JVM, where tests construct this
        // ViewModel with a mocked context). The screen still functions
        // without a controller — transport is inert — so degrade rather than
        // crash the whole player.
        val sessionToken = try {
            SessionToken(
                appContext,
                ComponentName(appContext, AudioPlayerService::class.java)
            )
        } catch (e: Exception) {
            Log.w("PlayerViewModel", "MediaController unavailable — session token failed", e)
            return
        }
        val futureController = MediaController.Builder(appContext, sessionToken).buildAsync()
        futureController.addListener({
            try {
                val mediaController = futureController.get()
                controller = mediaController

                // Listen for state changes
                mediaController.addListener(object : Player.Listener {
                    override fun onIsPlayingChanged(isPlaying: Boolean) {
                        _isPlaying.value = isPlaying
                    }
                    override fun onPlaybackParametersChanged(params: androidx.media3.common.PlaybackParameters) {
                        _speed.value = params.speed
                    }
                    override fun onPlaybackStateChanged(playbackState: Int) {
                        // Perform deferred seek when player is ready
                        if (playbackState == Player.STATE_READY && pendingSeekPosition >= 0) {
                            val pos = pendingSeekPosition
                            pendingSeekPosition = -1L
                            restoreSeek(mediaController, pos)
                            _positionMs.value = pos
                        }
                        // Track / audiobook reached its natural end — log a
                        // history entry so the session shows up as "finished".
                        // claimFormat=true: listening all the way to the end is
                        // the clearest possible consumption signal there is.
                        // The completion flag itself is written once, by
                        // AudioPlayerService's STATE_ENDED listener (which also
                        // fires for Auto/notification playback) — not here too
                        // (issue #56).
                        if (playbackState == Player.STATE_ENDED) {
                            saveBookmark(appendToLog = true, claimFormat = true)
                        }
                    }
                    override fun onMediaMetadataChanged(metadata: MediaMetadata) {
                        // Session-provided album art: decode sampled and off
                        // the main thread (issue #161) — a full-size decode of
                        // a 3000 px cover on the UI thread is visible jank.
                        metadata.artworkData?.let { artData ->
                            coverArtJob?.cancel()
                            coverArtJob = viewModelScope.launch(kotlinx.coroutines.Dispatchers.IO) {
                                try {
                                    com.booksync.auto.decodeEmbeddedArt(artData, maxPx = 1024)
                                        ?.let { _coverArtBitmap.value = it }
                                } catch (_: Exception) {}
                            }
                        }
                    }
                })

                // Start position polling
                startPositionPolling()

                // Fetch initial speed setting from service
                val futureCmd = mediaController.sendCustomCommand(SessionCommand(AudioPlayerService.CMD_GET_SPEED, Bundle()), Bundle())
                futureCmd.addListener({
                    try {
                        val result = futureCmd.get()
                        if (result.resultCode == SessionResult.RESULT_SUCCESS) {
                            val initialSpeed = result.extras.getFloat("speed", 1.0f)
                            _speed.value = initialSpeed
                        }
                    } catch (_: Exception) {}
                }, { it.run() })

                // Load media if pair or standalone audio is ready
                val standalone = _standaloneAudio.value
                if (standalone != null) {
                    loadStandaloneAudio(standalone, mediaController)
                } else {
                    _pair.value?.let { loadAudio(it, mediaController) }
                }

                // If bookmark was already loaded before controller connected,
                // seek to the saved position now. This handles the race condition
                // where the bookmark arrives before the controller is connected,
                // especially when the service is already running at a different position.
                if (bookmarkLoaded && savedPositionFromBookmark > 0) {
                    Log.d("PlayerViewModel", "connectToService: bookmark already loaded, seeking to $savedPositionFromBookmark, playbackState=${mediaController.playbackState}")
                    if (mediaController.playbackState == Player.STATE_READY) {
                        restoreSeek(mediaController, savedPositionFromBookmark)
                        _positionMs.value = savedPositionFromBookmark
                    } else {
                        pendingSeekPosition = savedPositionFromBookmark
                        _positionMs.value = savedPositionFromBookmark
                    }
                } else {
                    Log.d("PlayerViewModel", "connectToService: bookmarkLoaded=$bookmarkLoaded, savedPos=$savedPositionFromBookmark")
                }
            } catch (_: Exception) {}
        }, { it.run() })
    }

    /**
     * Extract embedded cover art off the main thread (issue #161): the old
     * inline blocks parsed a hundreds-of-MB container header and decoded the
     * art at full size on the UI thread on every player open. One in-flight
     * job; _coverArtBitmap is a StateFlow, safe to set from IO.
     */
    private fun loadCoverArt(audioFile: java.io.File) {
        if (_coverArtBitmap.value != null || coverArtJob?.isActive == true) return
        coverArtJob = viewModelScope.launch(kotlinx.coroutines.Dispatchers.IO) {
            com.booksync.auto.extractEmbeddedArt(audioFile)?.let { _coverArtBitmap.value = it }
        }
    }

    /**
     * Give the notification and lock screen something to show (issue #331).
     *
     * The phone built its MediaItem with a title and an artist and nothing else,
     * so media controls were artless for any book whose file had no embedded
     * art — while Android Auto, which does set `artworkUri`, was fine. Resolving
     * runs off the main thread because a cold cache means an HTTP fetch, and the
     * result is applied with `replaceMediaItem` on the same URI, which updates
     * metadata without disturbing playback.
     */
    private fun warmNotificationArtwork(
        audiobookId: Int,
        filename: String?,
        serverCoverPath: String?,
        mediaController: MediaController,
    ) {
        viewModelScope.launch {
            val uri = withContext(kotlinx.coroutines.Dispatchers.IO) {
                runCatching { coverArtHelper.getCoverUri(audiobookId, filename, serverCoverPath) }
                    .getOrNull()
            } ?: return@launch

            val current = mediaController.currentMediaItem ?: return@launch
            if (current.mediaMetadata.artworkUri != null) return@launch
            runCatching {
                mediaController.replaceMediaItem(
                    mediaController.currentMediaItemIndex,
                    current.buildUpon()
                        .setMediaMetadata(current.mediaMetadata.buildUpon().setArtworkUri(uri).build())
                        .build(),
                )
            }
        }
    }

    private fun loadAudio(pair: BookPairEntity, mediaController: MediaController) {
        // No `if (!pair.audiobookDownloaded) return` any more (issue #171): a
        // book that is not on the device streams from the server instead of
        // leaving the player with nothing loaded and every control dead.
        val audioFile = repository.localAudioFile(pair.audiobookFilename)
        val uri = mediaUriFor(audioFile, pair.audiobookId) ?: return

        // Only the downloaded file has embedded art to read; the streaming case
        // falls back to the server's cover, which the screen already fetches.
        if (audioFile != null && audioFile.isFile) loadCoverArt(audioFile)

        val mediaItem = MediaItem.Builder()
            .setMediaId(MediaId.Pair(pair.id).value)
            .setUri(uri)
            .setMediaMetadata(
                MediaMetadata.Builder()
                    .setTitle(pair.audiobookTitle)
                    .setArtist(pair.audiobookAuthor)
                    .build()
            )
            .build()

        // The notification and lock screen read artwork off the MediaItem, and
        // the phone's item never carried any — only the Android Auto path set it
        // (issue #331). Resolved in the background and applied when it lands, so
        // a cold cache costs a fetch rather than delaying playback.
        warmNotificationArtwork(pair.audiobookId, pair.audiobookFilename, pair.audiobookCoverPath, mediaController)

        // Only set if not already loaded (check current media item)
        val currentUri = mediaController.currentMediaItem?.localConfiguration?.uri
        if (currentUri != uri) {
            mediaController.setMediaItem(mediaItem)
            mediaController.prepare()

            // Defer position restore until player is STATE_READY
            val savedPosition = savedPositionFromBookmark
            if (savedPosition > 0) {
                pendingSeekPosition = savedPosition
                _positionMs.value = savedPosition
            }
        }
    }

    private fun loadStandaloneAudio(
        audio: com.booksync.data.local.entity.AudioBookEntity,
        mediaController: MediaController,
    ) {
        // Same as the paired path: not downloaded means stream, not refuse
        // (issue #171).
        val audioFile = repository.localAudioFile(audio.filename)
        val uri = mediaUriFor(audioFile, audio.id) ?: return

        if (audioFile != null && audioFile.isFile) loadCoverArt(audioFile)

        val mediaItem = MediaItem.Builder()
            .setMediaId(standaloneMediaId(audio))
            .setUri(uri)
            .setMediaMetadata(
                MediaMetadata.Builder()
                    .setTitle(audio.title)
                    .setArtist(audio.author)
                    .build()
            )
            .build()

        // See the paired path: artwork for the notification and lock screen.
        warmNotificationArtwork(audio.id, audio.filename, audio.coverFilename, mediaController)

        val currentUri = mediaController.currentMediaItem?.localConfiguration?.uri
        if (currentUri != uri) {
            mediaController.setMediaItem(mediaItem)
            mediaController.prepare()

            // No autoplay, same as the paired path: open at the restored
            // position, paused. The old unconditional play() here started
            // from 0:00 before the restore could land, and the boundary
            // saves then overwrote the real position (issue #142).
            val savedPosition = savedPositionFromBookmark
            if (savedPosition > 0) {
                pendingSeekPosition = savedPosition
                _positionMs.value = savedPosition
            }
        }
    }

    /**
     * The downloaded file if it is there, the server's stream URL otherwise
     * (issue #171). The single place this ViewModel turns a book into a URI;
     * pinned by `MediaSourceWiringTest`.
     */
    private fun mediaUriFor(localFile: java.io.File?, audiobookId: Int): Uri? =
        MediaSourceSelector.select(localFile, serverUrl, audiobookId)?.toUri()

    // `internal` + VisibleForTesting (issue #217): the loop is the one place this
    // screen touches the controller on a timer, and "it writes nothing" is a rule
    // worth asserting at runtime rather than only by reading the source.
    @androidx.annotation.VisibleForTesting
    internal fun startPositionPolling() {
        positionPollingJob?.cancel()
        positionPollingJob = viewModelScope.launch {
            while (true) {
                delay(500)
                val ctrl = controller ?: continue
                if (ctrl.isConnected) {
                    _positionMs.value = ctrl.currentPosition
                    _durationMs.value = maxOf(_durationMs.value, ctrl.duration.coerceAtLeast(0))
                    _isPlaying.value = ctrl.isPlaying

                    // Update current chapter index based on position
                    updateCurrentChapterIndex(ctrl.currentPosition)

                    // Try loading chapters once the player is ready
                    if (!chaptersLoaded && ctrl.playbackState == Player.STATE_READY) {
                        chaptersLoaded = true
                        loadChaptersFromService(ctrl)
                    }

                    // This loop writes NOTHING. AudioPlayerService owns every
                    // playback-position write — the periodic heartbeat, the
                    // 30-min tick, the seek flush and the pause boundary alike
                    // (see its startAutoPositionSave). It is driven by
                    // onIsPlayingChanged, so it covers this screen's playback
                    // too.
                    //
                    // This loop used to detect the pause edge here and save
                    // again, purely so the phone screen could claim the format
                    // (the service listener can't tell a deliberate pause from
                    // focus loss). That meant TWO PUT /api/sync/position calls
                    // within ~500 ms on every pause, three on "switch to
                    // reader" — doubled write volume, a doubled offline queue,
                    // and the app's own writes racing into 409s in the
                    // server's apply_position. The intent now travels as
                    // CMD_USER_PAUSE instead (issue #226, see togglePlayback);
                    // the write stays in one place.
                }
            }
        }
    }

    /**
     * Test seam (issue #226): supply the [MediaController] that
     * `connectToService` would normally hand over, so the JVM unit tests can
     * drive [togglePlayback] / [stopAndSave] without a live media session.
     * Nothing in production calls this.
     */
    @androidx.annotation.VisibleForTesting
    internal fun attachControllerForTest(mediaController: MediaController) {
        controller = mediaController
    }

    fun ensureMediaLoaded() {
        val ctrl = controller ?: return
        val standalone = _standaloneAudio.value
        if (standalone != null) {
            loadStandaloneAudio(standalone, ctrl)
        } else {
            val pair = _pair.value ?: return
            loadAudio(pair, ctrl)
        }
    }

    fun togglePlayback() {
        val ctrl = controller ?: return
        if (ctrl.isPlaying) {
            announceUserPause(ctrl)
            ctrl.pause()
        } else {
            // Ensure media is loaded before playing
            ensureMediaLoaded()
            // No rewind here. This screen used to seek back 5s itself, which left
            // Android Auto / notification / headset resumes with no rewind at all;
            // ResumeRewindPlayer now does it on the session player, so every
            // surface gets it. Doing it in both places jumps back 10s (issue #42).
            ctrl.play()
        }
    }

    fun seekTo(positionMs: Long) {
        controller?.seekTo(positionMs)
        _positionMs.value = positionMs
    }

    /**
     * Tell the service this next stop was asked for (issue #226).
     *
     * AudioPlayerService writes the position for every stop, but its
     * `onIsPlayingChanged` listener cannot tell a deliberate pause from audio
     * focus loss, a Bluetooth disconnect or the sleep timer, so it treats them
     * all as background saves. This screen is the only place that knows the
     * difference, and this command is the whole of its contribution — the
     * write itself stays with the service. Sending it before `pause()` arms
     * exactly one stop (see `PauseSavePolicy`).
     */
    private fun announceUserPause(ctrl: MediaController) {
        // Bundle() rather than the shared empty instance: the JVM unit tests link against
        // the stub android.jar, where the static EMPTY is null and
        // SessionCommand's checkNotNull(extras) throws. An empty Bundle costs
        // nothing and keeps this path unit-testable.
        ctrl.sendCustomCommand(
            SessionCommand(AudioPlayerService.CMD_USER_PAUSE, Bundle()),
            Bundle(),
        )
    }

    /**
     * A programmatic RESTORE seek (open-time position apply). Announces
     * itself to the service first so the user-seek flush (issue #166) skips
     * it — the has-played gate alone misses a reopen whose media item is
     * still loaded from the previous session (found live: the restore seek
     * was flushed with claimFormat=true on a mere screen open). User seeks
     * go through [seekTo], never through this.
     */
    private fun restoreSeek(ctrl: MediaController, positionMs: Long) {
        ctrl.sendCustomCommand(
            SessionCommand(AudioPlayerService.CMD_SUPPRESS_NEXT_SEEK_FLUSH, Bundle()),
            Bundle(),
        )
        ctrl.seekTo(positionMs)
    }

    fun skipForward() {
        val ctrl = controller ?: return
        seekTo(PlaybackOffsets.skipForwardPosition(ctrl.currentPosition, ctrl.duration))
    }

    fun skipBackward() {
        val ctrl = controller ?: return
        seekTo(PlaybackOffsets.skipBackPosition(ctrl.currentPosition))
    }

    fun skipToNextChapter() {
        val chaps = _chapters.value
        val currentIdx = _currentChapterIndex.value
        if (currentIdx < chaps.size - 1) {
            seekTo(chaps[currentIdx + 1].startMs)
        }
    }

    fun skipToPreviousChapter() {
        val chaps = _chapters.value
        val currentIdx = _currentChapterIndex.value
        val pos = _positionMs.value
        // If more than 3 seconds into the current chapter, restart it
        if (currentIdx >= 0 && currentIdx < chaps.size && pos - chaps[currentIdx].startMs > 3000) {
            seekTo(chaps[currentIdx].startMs)
        } else if (currentIdx > 0) {
            seekTo(chaps[currentIdx - 1].startMs)
        } else {
            seekTo(0L)
        }
    }

    private fun loadChaptersFromService(ctrl: MediaController) {
        val futureCmd = ctrl.sendCustomCommand(
            SessionCommand(AudioPlayerService.CMD_GET_CHAPTERS, Bundle()),
            Bundle()
        )
        futureCmd.addListener({
            try {
                val result = futureCmd.get()
                if (result.resultCode == SessionResult.RESULT_SUCCESS) {
                    val count = result.extras.getInt("count", 0)
                    if (count > 0) {
                        val titles = result.extras.getStringArray("titles") ?: emptyArray()
                        val startTimes = result.extras.getLongArray("startTimesMs") ?: longArrayOf()
                        val chapterList = titles.zip(startTimes.toList()).map { (title, startMs) ->
                            Chapter(title, startMs)
                        }
                        _chapters.value = chapterList
                        Log.d("PlayerViewModel", "Loaded ${chapterList.size} chapters from service")
                        updateCurrentChapterIndex(_positionMs.value)
                    } else {
                        Log.d("PlayerViewModel", "No chapters found in media")
                        // Try fallback: parse from file directly
                        loadChaptersFromFile()
                    }
                }
            } catch (e: Exception) {
                Log.w("PlayerViewModel", "Failed to load chapters from service", e)
                loadChaptersFromFile()
            }
        }, { it.run() })
    }

    /**
     * Fallback chapter loading: use FFmpeg-style chapter parsing from the M4B file.
     * This uses MediaMetadataRetriever which unfortunately doesn't support chapter
     * extraction. As a last resort, the chapters list stays empty and the UI shows
     * "No chapters found in this file".
     */
    private fun loadChaptersFromFile() {
        val audioFile: java.io.File? = when {
            isStandalone -> _standaloneAudio.value?.let { repository.localAudioFile(it.filename) }
            else -> _pair.value?.takeIf { it.audiobookDownloaded }?.let { repository.getAudiobookFile(it) }
        }
        if (audioFile == null || !audioFile.exists()) return

        viewModelScope.launch(kotlinx.coroutines.Dispatchers.IO) {
            try {
                // MediaMetadataRetriever doesn't expose M4B chapter markers.
                // In the future, we could add an ffprobe-based parser or a dedicated
                // MP4 chapter atom reader. For now, we leave chapters empty.
                Log.d("PlayerViewModel", "Fallback chapter loading: no chapters extracted from ${audioFile.name}")
            } catch (e: Exception) {
                Log.w("PlayerViewModel", "Error in fallback chapter loading", e)
            }
        }
    }

    private fun updateCurrentChapterIndex(positionMs: Long) {
        val chaps = _chapters.value
        if (chaps.isEmpty()) return
        // Find the last chapter whose startMs <= current position
        var idx = chaps.size - 1
        for (i in chaps.indices) {
            if (chaps[i].startMs > positionMs) {
                idx = maxOf(0, i - 1)
                break
            }
        }
        _currentChapterIndex.value = idx
    }

    fun cycleSpeed() {
        val currentIdx = SPEED_OPTIONS.indexOf(_speed.value)
        val nextIdx = (currentIdx + 1) % SPEED_OPTIONS.size
        val newSpeed = SPEED_OPTIONS[nextIdx]

        val ctrl = controller ?: return
        val args = Bundle().apply { putFloat("speed", newSpeed) }
        ctrl.sendCustomCommand(SessionCommand(AudioPlayerService.CMD_SET_SPEED, Bundle()), args)
        _speed.value = newSpeed
    }

    fun setSleepTimer(minutes: Int) {
        _sleepTimerMinutes.value = minutes
        _sleepTimerRemainingMs.value = minutes * 60 * 1000L

        val ctrl = controller ?: return
        val args = Bundle().apply { putInt("minutes", minutes) }
        ctrl.sendCustomCommand(SessionCommand(AudioPlayerService.CMD_SET_SLEEP_TIMER, Bundle()), args)

        // Start countdown display
        if (minutes > 0) {
            viewModelScope.launch {
                while (_sleepTimerRemainingMs.value > 0) {
                    delay(1000)
                    _sleepTimerRemainingMs.value = maxOf(0L, _sleepTimerRemainingMs.value - 1000L)
                }
                _sleepTimerMinutes.value = 0
            }
        }
    }

    fun cancelSleepTimer() {
        _sleepTimerMinutes.value = 0
        _sleepTimerRemainingMs.value = 0L
        val ctrl = controller ?: return
        val args = Bundle().apply { putInt("minutes", 0) }
        ctrl.sendCustomCommand(SessionCommand(AudioPlayerService.CMD_SET_SLEEP_TIMER, Bundle()), args)
    }

    /**
     * Save the current playback position.
     *
     * The ViewModel's last remaining position write, reached only from
     * [onCleared] (issue #226): AudioPlayerService owns the heartbeat, the
     * seek flush and the pause boundary. **Do not add a call site here.** If a
     * new moment needs a save, it belongs in the service, which sees every
     * playback surface — phone, Android Auto, notification and Cast.
     *
     * @param appendToLog false for position-only saves (no history entry).
     *   True only for a genuine session boundary — a BookmarkLog row plus a
     *   reset of the 30-min continuous-playback timer. Teardown is not one:
     *   the pause that preceded it already logged through the service.
     * @param claimFormat whether this save may claim "audiobook" as the format
     *   `resolvePairOpenTarget` routes to next (see
     *   [BookSyncRepository.savePlaybackPosition]'s doc for the full rule).
     *   Every call site here passes it explicitly — there is no default —
     *   so adding a new call site forces a conscious choice instead of
     *   silently inheriting whatever the last one happened to use.
     */
    private fun saveBookmark(appendToLog: Boolean = false, claimFormat: Boolean) {
        if (isStandalone) {
            // Write gate (issue #142): no save until the restore has been
            // *attempted* — a save issued first would write ~0 with a fresh
            // captured_at and destroy the real position everywhere. The
            // paired path is protected implicitly (it never autoplays and
            // its collect sets bookmarkLoaded); standalone is explicit.
            if (!bookmarkLoaded) return
            // Standalone audiobooks track progress locally only (no pair-linked bookmark)
            val audio = _standaloneAudio.value ?: return
            // State is read synchronously here, then the save runs detached on
            // the repository's app scope under NonCancellable (issue #165):
            // onCleared calls this AFTER viewModelScope is already cancelled,
            // so a viewModelScope.launch from there never ran its body.
            repository.savePlaybackPositionStandaloneDetached(
                audiobookId = audio.id,
                audioPositionMs = _positionMs.value.toInt(),
                claimFormat = claimFormat,
            )
            return
        }
        // One write. The server converts audio <-> epub position via the
        // SyncMap and projects the progress row the Continue list reads,
        // so there is no second call to keep in step. Detached for the same
        // reason as the standalone branch above.
        repository.savePlaybackPositionDetached(
            pairId = pairId,
            audioPositionMs = _positionMs.value.toInt(),
            appendToLog = appendToLog,
            claimFormat = claimFormat,
        )
    }

    /**
     * Pause playback on the way to the reader.
     *
     * The name is historical: this no longer saves. It used to `pause()` and
     * then `saveBookmark(appendToLog = true, claimFormat = true)`, which — on
     * top of the service's own pause listener and the poll loop's pause
     * detection — made "switch to reader" three position writes for one
     * boundary (issue #226). The pause is announced as a deliberate one and
     * AudioPlayerService does the single write with `claimFormat = true`.
     *
     * A stop that changes nothing (the player was already paused) writes
     * nothing, which is correct: the position was persisted by the pause that
     * got it here, and any seek since then was flushed by the service
     * (issue #166).
     */
    fun stopAndSave() {
        val ctrl = controller ?: return
        announceUserPause(ctrl)
        ctrl.pause()
    }

    override fun onCleared() {
        positionPollingJob?.cancel()
        // Save final position before cleanup. This is the one position write
        // left in this ViewModel (issue #226) and it stays because the
        // controller may already be disconnected here, so the service's own
        // teardown save is not guaranteed to have seen the last position.
        //
        // appendToLog=false: it is not a session boundary of its own. The
        // pause that preceded it already wrote the history row through the
        // service; logging again would put two entries on the same boundary.
        //
        // claimFormat reflects whether the player was actually playing at this
        // moment: if it's paused/idle at teardown, this save must not claim the
        // format (product rule — a background/idle save doesn't count as
        // consumption, even at session end).
        saveBookmark(appendToLog = false, claimFormat = _isPlaying.value)
        controller?.release()
        controller = null
        super.onCleared()
    }

    fun loadHistory() {
        viewModelScope.launch {
            _history.value = repository.getBookmarkHistory(pairId)
        }
    }

    fun markComplete() {
        viewModelScope.launch {
            val standalone = _standaloneAudio.value
            if (standalone != null) {
                repository.markComplete("audiobook", standalone.id)
                return@launch
            }
            val p = _pair.value ?: return@launch
            repository.markPairComplete(p.id, p.ebookId, p.audiobookId)
        }
    }

    fun resetProgress() {
        viewModelScope.launch {
            val standalone = _standaloneAudio.value
            if (standalone != null) {
                // Scoped DELETE — a true reset for standalone audio too
                // (issue #103). No-op locally if the server is unreachable.
                repository.resetStandaloneProgress("audiobook", standalone.id)
                return@launch
            }
            val p = _pair.value ?: return@launch
            // Pair-level DELETE removes the canonical bookmark + hints +
            // user_progress server-side and clears the matching local Room
            // caches. The old per-leg zero-write left the bookmark in place,
            // which re-seeded progress right back (issue: reset buttons not
            // actually resetting).
            repository.resetPairProgress(p.id)
        }
    }
}

// ============================================================================
// Player UI
// ============================================================================

private enum class PlayerTab { CHAPTERS, HISTORY }

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PlayerScreen(
    pairId: Int,
    onBack: () -> Unit,
    /** Receives the audio position at the moment of the switch — see Routes.READER. */
    onSwitchToReader: (Long) -> Unit,
    viewModel: PlayerViewModel = hiltViewModel(),
) {
    val colors = Tandem.colors

    val pair               by viewModel.pair.collectAsState()
    val standaloneAudio    by viewModel.standaloneAudio.collectAsState()
    val isStandalone       = viewModel.isStandalone
    val isPlaying          by viewModel.isPlaying.collectAsState()
    val positionMs         by viewModel.positionMs.collectAsState()
    val durationMs         by viewModel.durationMs.collectAsState()
    val speed              by viewModel.speed.collectAsState()
    val sleepTimerMinutes  by viewModel.sleepTimerMinutes.collectAsState()
    val sleepTimerRemainingMs by viewModel.sleepTimerRemainingMs.collectAsState()
    val coverArt           by viewModel.coverArtBitmap.collectAsState()
    val serverCoverPath    by viewModel.serverCoverPath.collectAsState()
    val serverUrl          = viewModel.serverUrl
    val chapters           by viewModel.chapters.collectAsState()
    val currentChapterIdx  by viewModel.currentChapterIndex.collectAsState()
    val historyItems       by viewModel.history.collectAsState()
    val downloadProgress   by viewModel.downloadProgress.collectAsState()
    val downloadError      by viewModel.downloadError.collectAsState()

    // Resolved title/author — prefer standalone audio entity, fall back to pair
    val displayTitle  = standaloneAudio?.title  ?: pair?.audiobookTitle  ?: "Audiobook"
    val displayAuthor = standaloneAudio?.author ?: pair?.audiobookAuthor

    val isDownloaded = pair?.audiobookDownloaded == true || standaloneAudio?.isDownloaded == true
    val isOnline           by viewModel.isOnline.collectAsState()
    val isCasting          by viewModel.isCasting.collectAsState()

    // Issue #171: an undownloaded book streams, so "downloaded" is no longer the
    // question the transport controls should be asking. The rules are pure and
    // live in PlayerTransportState.kt — Compose is not unit-testable here.
    val transportEnabled = transportEnabled(isDownloaded, isOnline, isCasting)
    val canCast          = castAvailable(isDownloaded)
    val downloadHint     = downloadHintMessage(isDownloaded, isOnline)

    var showSleepSheet  by remember { mutableStateOf(false) }
    var showOverflowMenu by remember { mutableStateOf(false) }
    var selectedTab     by remember { mutableStateOf(PlayerTab.CHAPTERS) }

    // Show download error as toast
    val context = LocalContext.current
    LaunchedEffect(downloadError) {
        downloadError?.let {
            android.widget.Toast.makeText(context, "Download error: $it", android.widget.Toast.LENGTH_LONG).show()
            viewModel.clearDownloadError()
        }
    }

    // Sleep-timer bottom sheet
    if (showSleepSheet) {
        SleepTimerSheet(
            activeMinutes = sleepTimerMinutes,
            remainingMs   = sleepTimerRemainingMs,
            onSet         = { mins -> viewModel.setSleepTimer(mins); showSleepSheet = false },
            onCancel      = { viewModel.cancelSleepTimer(); showSleepSheet = false },
            onDismiss     = { showSleepSheet = false },
        )
    }

    Scaffold(
        containerColor = colors.bgPrimary,
        topBar = {
            TopAppBar(
                title = { Text("Now Playing", color = colors.textPrimary, fontSize = 18.sp, fontWeight = FontWeight.SemiBold) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back", tint = colors.textPrimary)
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = colors.bgPrimary),
                actions = {
                    // Chromecast button. Disabled unless the book is on the phone:
                    // LocalCastHttpServer streams the phone's own copy over the LAN
                    // and the receiver cannot send a Bearer header, so there is no
                    // way to cast a streaming book (issue #171). Saying so with a
                    // dead button and a toast beats a receiver that 401s and idles.
                    AndroidView(
                        factory = { ctx ->
                            MediaRouteButton(ctx).also { btn ->
                                CastButtonFactory.setUpMediaRouteButton(ctx, btn)
                            }
                        },
                        update = { btn -> btn.isEnabled = canCast },
                        modifier = Modifier
                            .size(48.dp)
                            .then(
                                if (canCast) Modifier
                                else Modifier.clickable {
                                    android.widget.Toast.makeText(
                                        context,
                                        "Download this audiobook to cast it.",
                                        android.widget.Toast.LENGTH_SHORT,
                                    ).show()
                                }
                            ),
                    )
                    // Switch to Reader (not available for standalone audiobooks — no paired ebook)
                    if (!isStandalone) {
                        IconButton(onClick = { viewModel.stopAndSave(); onSwitchToReader(positionMs) }) {
                            Icon(Icons.Default.AutoStories, "Switch to Reader", tint = colors.textPrimary)
                        }
                    }
                    // Overflow (Mark Complete / Reset Progress)
                    Box {
                        IconButton(onClick = { showOverflowMenu = true }) {
                            Icon(Icons.Default.MoreVert, "More options", tint = colors.textPrimary)
                        }
                        DropdownMenu(
                            expanded = showOverflowMenu,
                            onDismissRequest = { showOverflowMenu = false },
                            containerColor = colors.bgSecondary,
                        ) {
                            DropdownMenuItem(
                                text = { Text("Mark complete", color = colors.textPrimary) },
                                onClick = { showOverflowMenu = false; viewModel.markComplete(); onBack() },
                            )
                            DropdownMenuItem(
                                text = { Text("Reset progress", color = colors.textPrimary) },
                                onClick = { showOverflowMenu = false; viewModel.resetProgress() },
                            )
                        }
                    }
                },
            )
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(horizontal = 24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {

            Spacer(Modifier.height(16.dp))

            // ── Cover art ────────────────────────────────────────────────────
            Box(
                modifier = Modifier
                    .size(220.dp)
                    .clip(Tandem.shapes.card)
                    .background(colors.bgCard),
                contentAlignment = Alignment.Center,
            ) {
                // Embedded art first — it is already decoded and works offline.
                // Then the server's cover (issue #331): before that fallback
                // existed, a book whose audio file carried no embedded art was
                // blank here while Home, Library, Details and the web UI all
                // showed its cover. Coil is wired to the auth-capable
                // OkHttpClient in BookSyncApp, so the bearer token is handled.
                val serverCover = serverCoverPath?.let { coverImageUrl(serverUrl, it) }
                when {
                    coverArt != null -> Image(
                        bitmap = coverArt!!.asImageBitmap(),
                        contentDescription = "Album Art",
                        modifier = Modifier.fillMaxSize(),
                        contentScale = ContentScale.Crop,
                    )

                    serverCover != null -> AsyncImage(
                        model = ImageRequest.Builder(LocalContext.current)
                            .data(serverCover)
                            .crossfade(true)
                            .build(),
                        contentDescription = "Album Art",
                        modifier = Modifier.fillMaxSize(),
                        contentScale = ContentScale.Crop,
                        // Only now is there genuinely no art to show.
                        error = rememberVectorPainter(Icons.Default.Headphones),
                    )

                    else -> Icon(
                        Icons.Default.Headphones,
                        contentDescription = null,
                        tint = colors.textMuted,
                        modifier = Modifier.size(64.dp),
                    )
                }
            }

            Spacer(Modifier.height(20.dp))

            // ── Title & author ───────────────────────────────────────────────
            Text(
                text = displayTitle,
                color = colors.textPrimary,
                fontSize = 20.sp,
                fontWeight = FontWeight.SemiBold,
                textAlign = TextAlign.Center,
                maxLines = 2,
            )
            displayAuthor?.let { author ->
                Spacer(Modifier.height(4.dp))
                Text(text = author, color = colors.textSecondary, fontSize = 14.sp, textAlign = TextAlign.Center)
            }

            // Download state (issue #171). Not a warning any more while the book is
            // streaming — that is the normal case, and the download is an offer:
            // offline listening, and Cast, which serves the phone's own copy. It
            // only turns into a warning offline, the one state where the controls
            // really are dead.
            if (!isDownloaded) {
                val hintIsError = !isOnline
                Spacer(Modifier.height(10.dp))
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(Tandem.shapes.input)
                        .background(
                            if (hintIsError) colors.statusError.copy(alpha = 0.15f)
                            else colors.bgCard
                        )
                        .padding(10.dp),
                ) {
                    val hintColor = if (hintIsError) colors.statusError else colors.textSecondary
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(
                            if (hintIsError) Icons.Default.Warning else Icons.Default.CloudDownload,
                            null,
                            tint = hintColor,
                            modifier = Modifier.size(16.dp),
                        )
                        Spacer(Modifier.size(8.dp))
                        Text(
                            if (downloadProgress != null) "Downloading audiobook…"
                            else downloadHint.orEmpty(),
                            color = hintColor,
                            fontSize = 13.sp,
                        )
                    }
                    if (downloadProgress != null) {
                        Spacer(Modifier.height(8.dp))
                        LinearProgressIndicator(
                            progress = { downloadProgress!! / 100f },
                            modifier = Modifier.fillMaxWidth(),
                            color = colors.accent,
                            trackColor = colors.border,
                        )
                    } else if (pair != null || standaloneAudio != null) {
                        Spacer(Modifier.height(8.dp))
                        Button(
                            onClick = {
                                if (isStandalone) viewModel.downloadStandaloneAudiobook()
                                else viewModel.downloadAudiobook()
                            },
                            modifier = Modifier.fillMaxWidth(),
                            colors = ButtonDefaults.buttonColors(
                                containerColor = colors.accent,
                                contentColor = colors.textPrimary,
                            ),
                        ) {
                            Icon(Icons.Default.Download, contentDescription = null, modifier = Modifier.size(16.dp))
                            Spacer(Modifier.size(6.dp))
                            Text("Download for offline", fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                        }
                    }
                }
            }

            Spacer(Modifier.height(20.dp))

            // ── Progress slider ──────────────────────────────────────────────
            val progress = if (durationMs > 0) positionMs.toFloat() / durationMs else 0f
            Slider(
                value = progress,
                onValueChange = { viewModel.seekTo((it * durationMs).toLong()) },
                modifier = Modifier.fillMaxWidth(),
                enabled = transportEnabled,
                colors = SliderDefaults.colors(
                    thumbColor = colors.accent,
                    activeTrackColor = colors.accent,
                    inactiveTrackColor = colors.border,
                ),
            )
            Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text(formatTime(positionMs), color = colors.textSecondary, fontSize = 12.sp)
                Text(formatTime(durationMs), color = colors.textSecondary, fontSize = 12.sp)
            }

            Spacer(Modifier.height(16.dp))

            // ── Playback controls ────────────────────────────────────────────
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceEvenly,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                // Prev chapter
                IconButton(
                    onClick = { viewModel.skipToPreviousChapter() },
                    enabled = transportEnabled && chapters.isNotEmpty(),
                ) {
                    Icon(Icons.Default.SkipPrevious, "Prev chapter", tint = colors.textPrimary, modifier = Modifier.size(28.dp))
                }

                // Replay 30 s — matches PlaybackOffsets.SKIP_MS.
                IconButton(onClick = { viewModel.skipBackward() }, enabled = transportEnabled) {
                    Icon(Icons.Default.Replay30, "Rewind 30s", tint = colors.textPrimary, modifier = Modifier.size(32.dp))
                }

                // Play / Pause FAB
                Box(
                    modifier = Modifier
                        .size(64.dp)
                        .clip(CircleShape)
                        .background(if (transportEnabled) colors.accent else colors.border)
                        .clickable(enabled = transportEnabled) { viewModel.togglePlayback() },
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(
                        imageVector = if (isPlaying) Icons.Default.Pause else Icons.Default.PlayArrow,
                        contentDescription = if (isPlaying) "Pause" else "Play",
                        tint = Color.White,
                        modifier = Modifier.size(36.dp),
                    )
                }

                // Forward 30 s — matches PlaybackOffsets.SKIP_MS.
                IconButton(onClick = { viewModel.skipForward() }, enabled = transportEnabled) {
                    Icon(Icons.Default.Forward30, "Forward 30s", tint = colors.textPrimary, modifier = Modifier.size(32.dp))
                }

                // Next chapter
                IconButton(
                    onClick = { viewModel.skipToNextChapter() },
                    enabled = transportEnabled && chapters.isNotEmpty(),
                ) {
                    Icon(Icons.Default.SkipNext, "Next chapter", tint = colors.textPrimary, modifier = Modifier.size(28.dp))
                }
            }

            Spacer(Modifier.height(14.dp))

            // ── Speed pill + Sleep timer ─────────────────────────────────────
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.Center,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                // Speed pill
                val speedLabel = if (speed % 1.0f == 0f) "%.1f×".format(speed) else "${speed}×"
                Box(
                    modifier = Modifier
                        .clip(Tandem.shapes.pill)
                        .background(colors.bgCard)
                        .clickable(enabled = transportEnabled) { viewModel.cycleSpeed() }
                        .padding(horizontal = 18.dp, vertical = 8.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(speedLabel, color = colors.textPrimary, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
                }

                Spacer(Modifier.width(24.dp))

                // Sleep timer icon — tinted when active
                IconButton(onClick = { showSleepSheet = true }, enabled = transportEnabled) {
                    Icon(
                        Icons.Default.NightsStay,
                        "Sleep timer",
                        tint = if (sleepTimerMinutes > 0) colors.accent else colors.textSecondary,
                        modifier = Modifier.size(24.dp),
                    )
                }
                if (sleepTimerMinutes > 0) {
                    Text(
                        formatTime(sleepTimerRemainingMs),
                        color = colors.accent,
                        fontSize = 12.sp,
                        fontWeight = FontWeight.Medium,
                    )
                }
            }

            Spacer(Modifier.height(16.dp))

            // ── Chapters / History tabs ──────────────────────────────────────
            TabRow(
                selectedTabIndex = selectedTab.ordinal,
                containerColor = colors.bgPrimary,
                contentColor = colors.accent,
                indicator = { tabPositions ->
                    if (selectedTab.ordinal < tabPositions.size) {
                        TabRowDefaults.SecondaryIndicator(
                            modifier = Modifier.tabIndicatorOffset(tabPositions[selectedTab.ordinal]),
                            color = colors.accent,
                        )
                    }
                },
            ) {
                PlayerTab.entries.forEach { tab ->
                    Tab(
                        selected = selectedTab == tab,
                        onClick = {
                            selectedTab = tab
                            if (tab == PlayerTab.HISTORY) viewModel.loadHistory()
                        },
                        text = {
                            Text(
                                text = tab.name.lowercase().replaceFirstChar { it.uppercase() },
                                color = if (selectedTab == tab) colors.accent else colors.textSecondary,
                                fontSize = 13.sp,
                                fontWeight = FontWeight.Medium,
                            )
                        },
                    )
                }
            }

            when (selectedTab) {
                PlayerTab.CHAPTERS -> ChaptersTab(
                    chapters = chapters,
                    currentIndex = currentChapterIdx,
                    colors = colors,
                    onChapterClick = { chapter -> viewModel.seekTo(chapter.startMs) },
                )
                PlayerTab.HISTORY  -> HistoryTab(
                    items = historyItems,
                    colors = colors,
                    onItemClick = { ms -> viewModel.seekTo(ms) },
                )
            }
        }
    }
}

// ── Sub-composables ─────────────────────────────────────────────────────────

@Composable
private fun ChaptersTab(
    chapters: List<Chapter>,
    currentIndex: Int,
    colors: com.booksync.ui.theme.TandemColors,
    onChapterClick: (Chapter) -> Unit,
) {
    if (chapters.isEmpty()) {
        Box(Modifier.fillMaxWidth().padding(vertical = 24.dp), contentAlignment = Alignment.Center) {
            Text("No chapters found in this file", color = colors.textMuted, fontSize = 14.sp)
        }
        return
    }
    LazyColumn(modifier = Modifier.fillMaxWidth()) {
        itemsIndexed(chapters) { index, chapter ->
            val isActive = index == currentIndex
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(if (isActive) colors.accent.copy(alpha = 0.12f) else Color.Transparent)
                    .clickable { onChapterClick(chapter) }
                    .padding(horizontal = 4.dp, vertical = 12.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    chapter.title,
                    color = if (isActive) colors.accent else colors.textPrimary,
                    fontSize = 14.sp,
                    fontWeight = if (isActive) FontWeight.SemiBold else FontWeight.Normal,
                    modifier = Modifier.weight(1f),
                )
                Text(formatTime(chapter.startMs), color = colors.textMuted, fontSize = 12.sp)
            }
            HorizontalDivider(color = colors.border.copy(alpha = 0.5f), thickness = 0.5.dp)
        }
    }
}

@Composable
private fun HistoryTab(
    items: List<BookmarkLogResponse>,
    colors: com.booksync.ui.theme.TandemColors,
    onItemClick: (Long) -> Unit,
) {
    if (items.isEmpty()) {
        Box(Modifier.fillMaxWidth().padding(vertical = 24.dp), contentAlignment = Alignment.Center) {
            Text("No history yet", color = colors.textMuted, fontSize = 14.sp)
        }
        return
    }
    LazyColumn(modifier = Modifier.fillMaxWidth()) {
        items(items) { item ->
            val posMs = item.new_audio_position_ms?.toLong()
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .clickable(enabled = posMs != null) { posMs?.let(onItemClick) }
                    .padding(horizontal = 4.dp, vertical = 12.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Icon(
                    imageVector = if (item.source == "audiobook") Icons.Default.Headphones
                                  else Icons.AutoMirrored.Filled.MenuBook,
                    contentDescription = null,
                    tint = colors.textSecondary,
                    modifier = Modifier.size(18.dp),
                )
                Spacer(Modifier.width(12.dp))
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = if (posMs != null) formatTime(posMs) else "Chapter ${item.new_epub_chapter ?: "?"}",
                        color = colors.textPrimary,
                        fontSize = 14.sp,
                    )
                    Text(
                        text = "${item.source} · ${formatAbsoluteTime(item.changed_at)}" +
                            historyDeviceSuffix(item.device_name, item.device_id),
                        color = colors.textMuted,
                        fontSize = 12.sp,
                    )
                }
            }
            HorizontalDivider(color = colors.border.copy(alpha = 0.5f), thickness = 0.5.dp)
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SleepTimerSheet(
    activeMinutes: Int,
    remainingMs: Long,
    onSet: (Int) -> Unit,
    onCancel: () -> Unit,
    onDismiss: () -> Unit,
) {
    val colors = Tandem.colors
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
        containerColor = colors.bgSecondary,
        shape = Tandem.shapes.modal,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 20.dp)
                .padding(bottom = 32.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Default.NightsStay, null, tint = colors.accent, modifier = Modifier.size(20.dp))
                Spacer(Modifier.size(8.dp))
                Text("Sleep timer", color = colors.textPrimary, fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
            }
            if (activeMinutes > 0) {
                Spacer(Modifier.height(8.dp))
                Text(
                    "Active: ${formatTime(remainingMs)} remaining",
                    color = colors.accent,
                    fontSize = 13.sp,
                )
                Spacer(Modifier.height(4.dp))
                Button(
                    onClick = onCancel,
                    modifier = Modifier.fillMaxWidth(),
                    shape = Tandem.shapes.button,
                    colors = ButtonDefaults.buttonColors(containerColor = colors.statusError),
                ) { Text("Cancel timer", color = Color.White) }
                HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp), color = colors.border)
            } else {
                Spacer(Modifier.height(8.dp))
            }
            listOf(5, 15, 30, 45, 60).forEach { minutes ->
                TextButton(
                    onClick = { onSet(minutes) },
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text("$minutes minutes", color = colors.textPrimary, fontSize = 15.sp)
                }
            }
        }
    }
}

// ── Formatting helpers ───────────────────────────────────────────────────────

private fun formatTime(ms: Long): String {
    val totalSeconds = (ms / 1000).toInt()
    val hours = totalSeconds / 3600
    val minutes = (totalSeconds % 3600) / 60
    val seconds = totalSeconds % 60
    return if (hours > 0) "%d:%02d:%02d".format(hours, minutes, seconds)
    else "%d:%02d".format(minutes, seconds)
}

/**
 * Format a server timestamp into a human-readable local time.
 * Server sends UTC timestamps like "2026-03-04T21:52:14.923182" (no timezone suffix).
 * We parse as UTC and convert to the device's local timezone.
 */
private fun formatAbsoluteTime(isoTimestamp: String): String {
    return try {
        val utcTime = java.time.LocalDateTime.parse(
            isoTimestamp,
            DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss[.SSSSSS][.SSSSS][.SSSS][.SSS][.SS][.S]")
        )
        val zonedUtc = utcTime.atZone(java.time.ZoneId.of("UTC"))
        val localTime = zonedUtc.withZoneSameInstant(java.time.ZoneId.systemDefault())
        localTime.format(DateTimeFormatter.ofPattern("MMM d, h:mm a", Locale.getDefault()))
    } catch (_: Exception) {
        isoTimestamp
    }
}

/**
 * Renders the trailing " · from {label}" suffix for a History-tab entry, preferring
 * a friendly device name over the raw device id, and rendering nothing at all when
 * neither is present (e.g. legacy log rows predating issue #54's device attribution).
 */
internal fun historyDeviceSuffix(deviceName: String?, deviceId: String?): String {
    val label = deviceName?.trim()?.takeIf { it.isNotBlank() } ?: deviceId
    return if (label.isNullOrBlank()) "" else " · from $label"
}

