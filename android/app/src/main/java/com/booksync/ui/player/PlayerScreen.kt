package com.booksync.ui.player

import android.content.ComponentName
import android.graphics.Bitmap
import android.graphics.BitmapFactory
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
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.layout.ContentScale
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
import com.booksync.player.AudioPlayerService
import com.booksync.ui.theme.Tandem
import com.booksync.worker.DownloadWorker
import com.google.android.gms.cast.framework.CastButtonFactory
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import android.content.Context
import android.os.Bundle
import android.util.Log
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull

import java.time.format.DateTimeFormatter
import java.util.Locale
import javax.inject.Inject

/**
 * How long the player waits for the server's position before falling back to
 * the local cache. Long enough for a normal request, short enough that an
 * unreachable server doesn't visibly delay playback.
 */
private const val SERVER_POSITION_TIMEOUT_MS = 1500L

/**
 * Represents a chapter marker in an M4B audiobook.
 */
data class Chapter(val title: String, val startMs: Long)

/**
 * Audio Player ViewModel.
 * Connects to AudioPlayerService via MediaController for real playback.
 */
@HiltViewModel
class PlayerViewModel @Inject constructor(
    private val repository: BookSyncRepository,
    @param:ApplicationContext private val appContext: Context,
    savedStateHandle: SavedStateHandle,
) : ViewModel() {
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
        val request = OneTimeWorkRequestBuilder<DownloadWorker>()
            .setInputData(workDataOf(
                DownloadWorker.KEY_PAIR_ID to audio.id,
                DownloadWorker.KEY_TYPE    to "STANDALONE_AUDIOBOOK",
            ))
            .addTag("download_worker")
            .build()
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
        val request = OneTimeWorkRequestBuilder<DownloadWorker>()
            .setInputData(workDataOf(
                DownloadWorker.KEY_PAIR_ID to pair.id,
                DownloadWorker.KEY_TYPE    to "AUDIOBOOK",
            ))
            .addTag("download_worker")
            .build()
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
            // Standalone audiobook mode — load by audiobookId, skip pair/bookmark loading
            viewModelScope.launch {
                val audio = repository.getAudiobookById(standaloneAudiobookId)
                _standaloneAudio.value = audio
                audio?.durationSeconds?.let { _durationMs.value = it * 1000L }
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
                                    ctrl.seekTo(pos.toLong())
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
        val sessionToken = SessionToken(
            appContext,
            ComponentName(appContext, AudioPlayerService::class.java)
        )
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
                            mediaController.seekTo(pos)
                            _positionMs.value = pos
                        }
                        // Track / audiobook reached its natural end — log a
                        // history entry so the session shows up as "finished".
                        // claimFormat=true: listening all the way to the end is
                        // the clearest possible consumption signal there is.
                        if (playbackState == Player.STATE_ENDED) {
                            saveBookmark(appendToLog = true, claimFormat = true)
                            markComplete()
                        }
                    }
                    override fun onMediaMetadataChanged(metadata: MediaMetadata) {
                        // Extract embedded album art from audio file
                        metadata.artworkData?.let { artData ->
                            try {
                                _coverArtBitmap.value = BitmapFactory.decodeByteArray(artData, 0, artData.size)
                            } catch (_: Exception) {}
                        }
                    }
                })

                // Start position polling
                startPositionPolling()

                // Fetch initial speed setting from service
                val futureCmd = mediaController.sendCustomCommand(SessionCommand(AudioPlayerService.CMD_GET_SPEED, Bundle.EMPTY), Bundle.EMPTY)
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
                        mediaController.seekTo(savedPositionFromBookmark)
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

    private fun loadAudio(pair: BookPairEntity, mediaController: MediaController) {
        if (!pair.audiobookDownloaded) return

        val audioFile = repository.getAudiobookFile(pair)
        if (!audioFile.exists()) return

        // Extract cover art from audio file if not already loaded
        if (_coverArtBitmap.value == null) {
            try {
                val retriever = android.media.MediaMetadataRetriever()
                retriever.setDataSource(audioFile.absolutePath)
                val artBytes = retriever.embeddedPicture
                if (artBytes != null) {
                    _coverArtBitmap.value = BitmapFactory.decodeByteArray(artBytes, 0, artBytes.size)
                }
                retriever.release()
            } catch (_: Exception) {}
        }

        val mediaItem = MediaItem.Builder()
            .setMediaId("pair_${pair.id}")
            .setUri(Uri.fromFile(audioFile))
            .setMediaMetadata(
                MediaMetadata.Builder()
                    .setTitle(pair.audiobookTitle)
                    .setArtist(pair.audiobookAuthor)
                    .build()
            )
            .build()

        // Only set if not already loaded (check current media item)
        val currentUri = mediaController.currentMediaItem?.localConfiguration?.uri
        if (currentUri != Uri.fromFile(audioFile)) {
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
        if (!audio.isDownloaded) return
        val audioFile = java.io.File(appContext.filesDir, "audiobooks/${audio.filename}")
        if (!audioFile.exists()) return

        // Extract cover art from audio file if not already loaded
        if (_coverArtBitmap.value == null) {
            try {
                val retriever = android.media.MediaMetadataRetriever()
                retriever.setDataSource(audioFile.absolutePath)
                val artBytes = retriever.embeddedPicture
                if (artBytes != null) {
                    _coverArtBitmap.value = BitmapFactory.decodeByteArray(artBytes, 0, artBytes.size)
                }
                retriever.release()
            } catch (_: Exception) {}
        }

        val mediaItem = MediaItem.Builder()
            .setMediaId("standalone_${audio.id}")
            .setUri(Uri.fromFile(audioFile))
            .setMediaMetadata(
                MediaMetadata.Builder()
                    .setTitle(audio.title)
                    .setArtist(audio.author)
                    .build()
            )
            .build()

        val currentUri = mediaController.currentMediaItem?.localConfiguration?.uri
        if (currentUri != Uri.fromFile(audioFile)) {
            mediaController.setMediaItem(mediaItem)
            mediaController.prepare()
            mediaController.play()
        }
    }

    private fun startPositionPolling() {
        positionPollingJob?.cancel()
        positionPollingJob = viewModelScope.launch {
            while (true) {
                delay(500)
                val ctrl = controller ?: continue
                if (ctrl.isConnected) {
                    _positionMs.value = ctrl.currentPosition
                    _durationMs.value = maxOf(_durationMs.value, ctrl.duration.coerceAtLeast(0))
                    val wasPlaying = _isPlaying.value
                    _isPlaying.value = ctrl.isPlaying

                    // Update current chapter index based on position
                    updateCurrentChapterIndex(ctrl.currentPosition)

                    // Try loading chapters once the player is ready
                    if (!chaptersLoaded && ctrl.playbackState == Player.STATE_READY) {
                        chaptersLoaded = true
                        loadChaptersFromService(ctrl)
                    }

                    // No heartbeat or 30-min tick here: AudioPlayerService owns
                    // the one periodic save (see its startAutoPositionSave).
                    // This screen used to run an identical 5-second loop, so
                    // whenever the player was open every tick produced TWO
                    // server writes — doubling write volume and the offline
                    // queue, and making the app's own two near-simultaneous
                    // writes race into 409s in the server's apply_position.
                    // The service loop is driven by onIsPlayingChanged, so it
                    // covers this screen's playback too.
                    //
                    // Pause → log this as a session boundary. This is the phone
                    // screen's own pause detection (as opposed to
                    // AudioPlayerService's, which also serves Android Auto and
                    // can't tell a deliberate pause from an involuntary one) —
                    // claimFormat=true: an explicit playback command, not a
                    // background save.
                    if (wasPlaying && !ctrl.isPlaying) {
                        saveBookmark(appendToLog = true, claimFormat = true)
                    }
                }
            }
        }
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
            ctrl.pause()
        } else {
            // Ensure media is loaded before playing
            ensureMediaLoaded()
            // Rewind 5 seconds on resume to help listener reorient
            val newPos = maxOf(0L, ctrl.currentPosition - 5000L)
            ctrl.seekTo(newPos)
            _positionMs.value = newPos
            ctrl.play()
        }
    }

    fun seekTo(positionMs: Long) {
        controller?.seekTo(positionMs)
        _positionMs.value = positionMs
    }

    fun skipForward(seconds: Int = 30) {
        val ctrl = controller ?: return
        val newPos = minOf(ctrl.currentPosition + seconds * 1000L, ctrl.duration.coerceAtLeast(0))
        seekTo(newPos)
    }

    fun skipBackward(seconds: Int = 10) {
        val ctrl = controller ?: return
        val newPos = maxOf(0L, ctrl.currentPosition - seconds * 1000L)
        seekTo(newPos)
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
            SessionCommand(AudioPlayerService.CMD_GET_CHAPTERS, Bundle.EMPTY),
            Bundle.EMPTY
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
            isStandalone -> _standaloneAudio.value?.let { java.io.File(appContext.filesDir, "audiobooks/${it.filename}") }
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
        ctrl.sendCustomCommand(SessionCommand(AudioPlayerService.CMD_SET_SPEED, Bundle.EMPTY), args)
        _speed.value = newSpeed
    }

    fun setSleepTimer(minutes: Int) {
        _sleepTimerMinutes.value = minutes
        _sleepTimerRemainingMs.value = minutes * 60 * 1000L

        val ctrl = controller ?: return
        val args = Bundle().apply { putInt("minutes", minutes) }
        ctrl.sendCustomCommand(SessionCommand(AudioPlayerService.CMD_SET_SLEEP_TIMER, Bundle.EMPTY), args)

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
        ctrl.sendCustomCommand(SessionCommand(AudioPlayerService.CMD_SET_SLEEP_TIMER, Bundle.EMPTY), args)
    }

    /**
     * Save the current playback position.
     *
     * @param appendToLog false for 5-second heartbeat saves (position-only, no
     *   history entry). True for pause / stop / 30-min-tick boundaries — those
     *   produce a BookmarkLog row and reset the 30-min continuous-playback timer.
     * @param claimFormat whether this save may claim "audiobook" as the format
     *   `resolvePairOpenTarget` routes to next (see
     *   [BookSyncRepository.savePlaybackPosition]'s doc for the full rule).
     *   Every call site here passes it explicitly — there is no default —
     *   so adding a new call site forces a conscious choice instead of
     *   silently inheriting whatever the last one happened to use.
     */
    private fun saveBookmark(appendToLog: Boolean = false, claimFormat: Boolean) {
        if (isStandalone) {
            // Standalone audiobooks track progress locally only (no pair-linked bookmark)
            val audio = _standaloneAudio.value ?: return
            viewModelScope.launch {
                try {
                    repository.savePlaybackPositionStandalone(
                        audiobookId = audio.id,
                        audioPositionMs = _positionMs.value.toInt(),
                        claimFormat = claimFormat,
                    )
                } catch (_: Exception) {}
            }
            return
        }
        viewModelScope.launch {
            try {
                // One write. The server converts audio <-> epub position via the
                // SyncMap and projects the progress row the Continue list reads,
                // so there is no second call to keep in step.
                repository.savePlaybackPosition(
                    pairId = pairId,
                    audioPositionMs = _positionMs.value.toInt(),
                    appendToLog = appendToLog,
                    claimFormat = claimFormat,
                )
            } catch (_: Exception) {}
        }
    }

    /**
     * Pause playback and save bookmark. Called when switching to reader — this
     * is a session boundary so we do log a history entry. claimFormat=true:
     * an explicit user command (the "switch to reader" button), not a
     * background save.
     */
    fun stopAndSave() {
        controller?.pause()
        saveBookmark(appendToLog = true, claimFormat = true)
    }

    override fun onCleared() {
        positionPollingJob?.cancel()
        // Save final position before cleanup — user closing the player is a
        // stop event. claimFormat reflects whether the player was actually
        // playing at this moment: if it's paused/idle at teardown, this save
        // must not claim the format (product rule — a background/idle save
        // doesn't count as consumption, even at session end).
        saveBookmark(appendToLog = true, claimFormat = _isPlaying.value)
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
            p.audiobookId?.let { repository.markComplete("audiobook", it) }
            p.ebookId?.let { repository.markComplete("ebook", it) }
        }
    }

    fun resetProgress() {
        viewModelScope.launch {
            val standalone = _standaloneAudio.value
            if (standalone != null) {
                // No pair to delete server-side for standalone audio — the
                // server's reset DELETE is pair-scoped only.
                repository.resetMediaProgress("audiobook", standalone.id)
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
    onSwitchToReader: () -> Unit,
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
    val chapters           by viewModel.chapters.collectAsState()
    val currentChapterIdx  by viewModel.currentChapterIndex.collectAsState()
    val historyItems       by viewModel.history.collectAsState()
    val downloadProgress   by viewModel.downloadProgress.collectAsState()
    val downloadError      by viewModel.downloadError.collectAsState()

    // Resolved title/author — prefer standalone audio entity, fall back to pair
    val displayTitle  = standaloneAudio?.title  ?: pair?.audiobookTitle  ?: "Audiobook"
    val displayAuthor = standaloneAudio?.author ?: pair?.audiobookAuthor

    val isDownloaded = pair?.audiobookDownloaded == true || standaloneAudio?.isDownloaded == true

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
                    // Chromecast button
                    AndroidView(
                        factory = { ctx ->
                            MediaRouteButton(ctx).also { btn ->
                                CastButtonFactory.setUpMediaRouteButton(ctx, btn)
                            }
                        },
                        modifier = Modifier.size(48.dp),
                    )
                    // Switch to Reader (not available for standalone audiobooks — no paired ebook)
                    if (!isStandalone) {
                        IconButton(onClick = { viewModel.stopAndSave(); onSwitchToReader() }) {
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
                if (coverArt != null) {
                    Image(
                        bitmap = coverArt!!.asImageBitmap(),
                        contentDescription = "Album Art",
                        modifier = Modifier.fillMaxSize(),
                        contentScale = ContentScale.Crop,
                    )
                } else {
                    Icon(
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

            // Download state — warning + button when not downloaded, progress bar when downloading
            if (!isDownloaded) {
                Spacer(Modifier.height(10.dp))
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(Tandem.shapes.input)
                        .background(colors.statusError.copy(alpha = 0.15f))
                        .padding(10.dp),
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Default.Warning, null, tint = colors.statusError, modifier = Modifier.size(16.dp))
                        Spacer(Modifier.size(8.dp))
                        Text(
                            if (downloadProgress != null) "Downloading audiobook…"
                            else "Audiobook not downloaded.",
                            color = colors.statusError,
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
                            Text("Download Audiobook", fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
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
                enabled = isDownloaded,
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
                    enabled = isDownloaded && chapters.isNotEmpty(),
                ) {
                    Icon(Icons.Default.SkipPrevious, "Prev chapter", tint = colors.textPrimary, modifier = Modifier.size(28.dp))
                }

                // Replay 15 s — Material only ships Replay5/10/30. Overlay "15"
                // on the plain Replay arrow so the glyph matches the behaviour.
                IconButton(onClick = { viewModel.skipBackward(15) }, enabled = isDownloaded) {
                    Skip15Icon(forward = false, tint = colors.textPrimary, size = 32.dp)
                }

                // Play / Pause FAB
                Box(
                    modifier = Modifier
                        .size(64.dp)
                        .clip(CircleShape)
                        .background(if (isDownloaded) colors.accent else colors.border)
                        .clickable(enabled = isDownloaded) { viewModel.togglePlayback() },
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(
                        imageVector = if (isPlaying) Icons.Default.Pause else Icons.Default.PlayArrow,
                        contentDescription = if (isPlaying) "Pause" else "Play",
                        tint = Color.White,
                        modifier = Modifier.size(36.dp),
                    )
                }

                // Forward 15 s — see Skip15Icon comment above.
                IconButton(onClick = { viewModel.skipForward(15) }, enabled = isDownloaded) {
                    Skip15Icon(forward = true, tint = colors.textPrimary, size = 32.dp)
                }

                // Next chapter
                IconButton(
                    onClick = { viewModel.skipToNextChapter() },
                    enabled = isDownloaded && chapters.isNotEmpty(),
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
                        .clickable(enabled = isDownloaded) { viewModel.cycleSpeed() }
                        .padding(horizontal = 18.dp, vertical = 8.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(speedLabel, color = colors.textPrimary, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
                }

                Spacer(Modifier.width(24.dp))

                // Sleep timer icon — tinted when active
                IconButton(onClick = { showSleepSheet = true }, enabled = isDownloaded) {
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

/**
 * Plain curved arrow + "15" numeral overlay. Material ships Forward/Replay
 * 5/10/30 but not 15, and the previous "Forward10" glyph made users think the
 * button jumped 10 s when it actually jumps 15. This composable overlays a
 * bold "15" on top of the plain [Icons.Default.Replay] arrow (mirrored on X
 * for forward), matching the visual weight of the Material Forward/Replay 10
 * style without needing a hand-rolled VectorDrawable.
 */
@Composable
private fun Skip15Icon(
    forward: Boolean,
    tint: Color,
    size: androidx.compose.ui.unit.Dp,
) {
    Box(
        modifier = Modifier.size(size),
        contentAlignment = Alignment.Center,
    ) {
        Icon(
            imageVector = Icons.Default.Replay,
            contentDescription = if (forward) "Forward 15s" else "Rewind 15s",
            tint = tint,
            modifier = Modifier
                .fillMaxSize()
                .graphicsLayer { if (forward) scaleX = -1f },
        )
        // Tuck the digits inside the circular-arrow curve. The Material
        // Replay/Forward arrow has its arrowhead at the top-left, so the
        // negative space where the "10" normally sits is the lower-center.
        Text(
            text = "15",
            color = tint,
            fontSize = 9.sp,
            fontWeight = FontWeight.Bold,
            modifier = Modifier.padding(top = (size.value * 0.25f).dp),
        )
    }
}
