package com.booksync.ui.player

import android.content.ComponentName
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.viewModelScope
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.Player
import androidx.media3.session.MediaController
import androidx.media3.session.SessionCommand
import androidx.media3.session.SessionResult
import androidx.media3.session.SessionToken
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.repository.BookSyncRepository
import com.booksync.player.AudioPlayerService
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import android.content.Context
import android.os.Bundle
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

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

    private val _pair = MutableStateFlow<BookPairEntity?>(null)
    val pair = _pair.asStateFlow()

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

    private var controller: MediaController? = null
    private var positionPollingJob: kotlinx.coroutines.Job? = null
    private var savedPositionFromBookmark: Long = 0L
    private var bookmarkLoaded = false
    private var lastSaveTimeMs = 0L
    private var pendingSeekPosition: Long = -1L  // Seek deferred until player is ready
    private val SAVE_INTERVAL_MS = 5000L  // Save bookmark every 5 seconds

    companion object {
        val SPEED_OPTIONS = listOf(0.5f, 0.75f, 1.0f, 1.25f, 1.5f, 2.0f)
    }

    init {
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
        viewModelScope.launch {
            repository.refreshBookmark(pairId)
            repository.getBookmarkFlow(pairId).collect { bm ->
                bm?.audioPositionMs?.let { pos ->
                    savedPositionFromBookmark = pos.toLong()
                    if (!bookmarkLoaded) {
                        bookmarkLoaded = true
                        _positionMs.value = pos.toLong()
                        // If controller is already ready, seek to saved position
                        controller?.let { ctrl ->
                            if (ctrl.isConnected && pos > 0) {
                                if (ctrl.playbackState == Player.STATE_READY) {
                                    ctrl.seekTo(pos.toLong())
                                } else {
                                    pendingSeekPosition = pos.toLong()
                                }
                            }
                        }
                    }
                }
            }
        }
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

                // Load media if pair is ready
                _pair.value?.let { loadAudio(it, mediaController) }
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

                    // Save bookmark periodically while playing (every 5 seconds)
                    val now = System.currentTimeMillis()
                    if (ctrl.isPlaying && (now - lastSaveTimeMs >= SAVE_INTERVAL_MS)) {
                        lastSaveTimeMs = now
                        saveBookmark()
                    }
                    // Save when playback pauses
                    if (wasPlaying && !ctrl.isPlaying) {
                        saveBookmark()
                    }
                }
            }
        }
    }

    fun ensureMediaLoaded() {
        val pair = _pair.value ?: return
        val ctrl = controller ?: return
        loadAudio(pair, ctrl)
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

    private fun saveBookmark() {
        viewModelScope.launch {
            try {
                val posMs = _positionMs.value.toInt()
                // Server handles epub<->audio position conversion via SyncMap
                repository.updateBookmark(
                    pairId = pairId,
                    source = "audiobook",
                    audioPositionMs = posMs,
                )
            } catch (_: Exception) {}
        }
    }

    override fun onCleared() {
        positionPollingJob?.cancel()
        // Save final position before cleanup
        saveBookmark()
        controller?.release()
        controller = null
        super.onCleared()
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PlayerScreen(
    pairId: Int,
    onBack: () -> Unit,
    onSwitchToReader: () -> Unit,
    viewModel: PlayerViewModel = hiltViewModel(),
) {
    val pair by viewModel.pair.collectAsState()
    val isPlaying by viewModel.isPlaying.collectAsState()
    val positionMs by viewModel.positionMs.collectAsState()
    val durationMs by viewModel.durationMs.collectAsState()
    val speed by viewModel.speed.collectAsState()
    val sleepTimerMinutes by viewModel.sleepTimerMinutes.collectAsState()
    val sleepTimerRemainingMs by viewModel.sleepTimerRemainingMs.collectAsState()
    val coverArt by viewModel.coverArtBitmap.collectAsState()

    var showSleepTimerDialog by remember { mutableStateOf(false) }

    // Not downloaded warning
    val isDownloaded = pair?.audiobookDownloaded == true

    // Sleep timer dialog
    if (showSleepTimerDialog) {
        AlertDialog(
            onDismissRequest = { showSleepTimerDialog = false },
            title = { Text("Sleep Timer") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    if (sleepTimerMinutes > 0) {
                        Text(
                            "Timer active: ${formatTime(sleepTimerRemainingMs)} remaining",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.primary,
                        )
                        Spacer(Modifier.height(8.dp))
                        FilledTonalButton(
                            onClick = {
                                viewModel.cancelSleepTimer()
                                showSleepTimerDialog = false
                            },
                            modifier = Modifier.fillMaxWidth(),
                        ) { Text("Cancel Timer") }
                        HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))
                    }
                    listOf(15, 30, 45, 60).forEach { minutes ->
                        TextButton(
                            onClick = {
                                viewModel.setSleepTimer(minutes)
                                showSleepTimerDialog = false
                            },
                            modifier = Modifier.fillMaxWidth(),
                        ) { Text("$minutes minutes") }
                    }
                }
            },
            confirmButton = {
                TextButton(onClick = { showSleepTimerDialog = false }) { Text("Close") }
            },
        )
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Now Playing") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back")
                    }
                },
                actions = {
                    FilledTonalIconButton(onClick = onSwitchToReader) {
                        Icon(Icons.Default.AutoStories, "Switch to Reader")
                    }
                },
            )
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(32.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            // Album art
            Card(
                modifier = Modifier
                    .size(240.dp)
                    .padding(bottom = 24.dp),
                colors = CardDefaults.cardColors(
                    containerColor = MaterialTheme.colorScheme.primaryContainer,
                ),
            ) {
                if (coverArt != null) {
                    Image(
                        bitmap = coverArt!!.asImageBitmap(),
                        contentDescription = "Album Art",
                        modifier = Modifier
                            .fillMaxSize()
                            .clip(MaterialTheme.shapes.medium),
                        contentScale = ContentScale.Crop,
                    )
                } else {
                    Box(
                        modifier = Modifier.fillMaxSize(),
                        contentAlignment = Alignment.Center,
                    ) {
                        Text("🎧", fontSize = 64.sp)
                    }
                }
            }

            // Title & Author
            Text(
                text = pair?.audiobookTitle ?: "Audiobook",
                style = MaterialTheme.typography.headlineSmall,
                fontWeight = FontWeight.Bold,
                textAlign = TextAlign.Center,
            )
            pair?.audiobookAuthor?.let { author ->
                Text(
                    text = author,
                    style = MaterialTheme.typography.bodyLarge,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            Spacer(Modifier.height(8.dp))

            // Download warning
            if (!isDownloaded) {
                Card(
                    colors = CardDefaults.cardColors(
                        containerColor = MaterialTheme.colorScheme.errorContainer,
                    ),
                ) {
                    Text(
                        "⚠️ Audiobook not downloaded. Download it first to play.",
                        modifier = Modifier.padding(12.dp),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onErrorContainer,
                    )
                }
                Spacer(Modifier.height(8.dp))
            }

            Spacer(Modifier.height(24.dp))

            // Progress slider
            val progress = if (durationMs > 0) positionMs.toFloat() / durationMs else 0f
            Slider(
                value = progress,
                onValueChange = { viewModel.seekTo((it * durationMs).toLong()) },
                modifier = Modifier.fillMaxWidth(),
                enabled = isDownloaded,
            )

            // Time labels
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Text(
                    text = formatTime(positionMs),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Text(
                    text = formatTime(durationMs),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            Spacer(Modifier.height(16.dp))

            // Playback controls
            Row(
                horizontalArrangement = Arrangement.spacedBy(16.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                IconButton(onClick = { viewModel.skipBackward(10) }, enabled = isDownloaded) {
                    Icon(Icons.Default.Replay10, "Rewind 10s", modifier = Modifier.size(32.dp))
                }

                FilledIconButton(
                    onClick = { viewModel.togglePlayback() },
                    modifier = Modifier.size(64.dp),
                    enabled = isDownloaded,
                ) {
                    Icon(
                        if (isPlaying) Icons.Default.Pause else Icons.Default.PlayArrow,
                        "Play/Pause",
                        modifier = Modifier.size(32.dp),
                    )
                }

                IconButton(onClick = { viewModel.skipForward(30) }, enabled = isDownloaded) {
                    Icon(Icons.Default.Forward30, "Forward 30s", modifier = Modifier.size(32.dp))
                }
            }

            Spacer(Modifier.height(16.dp))

            // Speed + Sleep Timer row
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceEvenly,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                // Speed button
                FilledTonalButton(onClick = { viewModel.cycleSpeed() }, enabled = isDownloaded) {
                    val speedText = if (speed % 1.0f == 0f) {
                        "%.1f×".format(speed)
                    } else {
                        "${speed}×"
                    }
                    Text(speedText, fontWeight = FontWeight.Bold)
                }

                // Sleep timer button
                FilledTonalButton(
                    onClick = { showSleepTimerDialog = true },
                    enabled = isDownloaded,
                ) {
                    Icon(Icons.Default.Timer, null, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(4.dp))
                    if (sleepTimerMinutes > 0) {
                        Text(formatTime(sleepTimerRemainingMs))
                    } else {
                        Text("Sleep")
                    }
                }
            }

            Spacer(Modifier.height(24.dp))

            // Switch to reader CTA
            OutlinedButton(
                onClick = onSwitchToReader,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Icon(Icons.Default.AutoStories, null)
                Spacer(Modifier.width(8.dp))
                Text("Switch to Reading")
            }
        }
    }
}

private fun formatTime(ms: Long): String {
    val totalSeconds = (ms / 1000).toInt()
    val hours = totalSeconds / 3600
    val minutes = (totalSeconds % 3600) / 60
    val seconds = totalSeconds % 60
    return if (hours > 0) {
        "%d:%02d:%02d".format(hours, minutes, seconds)
    } else {
        "%d:%02d".format(minutes, seconds)
    }
}
