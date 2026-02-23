package com.booksync.ui.player

import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.viewModelScope
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.repository.BookSyncRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * Audio Player ViewModel.
 * Manages playback state and position tracking.
 * In full implementation, uses Media3 ExoPlayer service.
 */
@HiltViewModel
class PlayerViewModel @Inject constructor(
    private val repository: BookSyncRepository,
    savedStateHandle: SavedStateHandle,
) : ViewModel() {
    private val pairId: Int = savedStateHandle["pairId"] ?: 0

    private val _pair = MutableStateFlow<BookPairEntity?>(null)
    val pair = _pair.asStateFlow()

    private val _isPlaying = MutableStateFlow(false)
    val isPlaying = _isPlaying.asStateFlow()

    private val _positionMs = MutableStateFlow(0)
    val positionMs = _positionMs.asStateFlow()

    private val _durationMs = MutableStateFlow(0)
    val durationMs = _durationMs.asStateFlow()

    private val _currentEpubChapter = MutableStateFlow(0)
    val currentEpubChapter = _currentEpubChapter.asStateFlow()

    private val _currentEpubSentence = MutableStateFlow(0)
    val currentEpubSentence = _currentEpubSentence.asStateFlow()

    init {
        viewModelScope.launch {
            repository.getPairsFlow().collect { pairs ->
                val found = pairs.find { it.id == pairId }
                _pair.value = found
                found?.audiobookDurationSeconds?.let {
                    _durationMs.value = it * 1000
                }
            }
        }
        viewModelScope.launch {
            repository.refreshBookmark(pairId)
            repository.getBookmarkFlow(pairId).collect { bm ->
                bm?.audioPositionMs?.let { _positionMs.value = it }
            }
        }
    }

    fun togglePlayback() {
        _isPlaying.value = !_isPlaying.value
        // In full implementation, this would control Media3 ExoPlayer
    }

    fun seekTo(positionMs: Int) {
        _positionMs.value = positionMs
        updateAudioPosition(positionMs)
    }

    fun skipForward(seconds: Int = 30) {
        val newPos = minOf(_positionMs.value + seconds * 1000, _durationMs.value)
        seekTo(newPos)
    }

    fun skipBackward(seconds: Int = 10) {
        val newPos = maxOf(0, _positionMs.value - seconds * 1000)
        seekTo(newPos)
    }

    fun updateAudioPosition(positionMs: Int) {
        viewModelScope.launch {
            val (chapter, sentence) = repository.audioToEpub(pairId, positionMs)
            _currentEpubChapter.value = chapter
            _currentEpubSentence.value = sentence
            repository.updateBookmark(
                pairId = pairId,
                source = "audio",
                audioPositionMs = positionMs,
                epubChapter = chapter,
                epubSentenceIndex = sentence,
            )
        }
    }

    /**
     * Start playback from the EPUB position, with rewind applied.
     */
    fun startFromEpubPosition(chapter: Int, sentenceIndex: Int) {
        viewModelScope.launch {
            val audioMs = repository.epubToAudio(pairId, chapter, sentenceIndex)
            seekTo(audioMs)
            _isPlaying.value = true
        }
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
    val epubChapter by viewModel.currentEpubChapter.collectAsState()
    val epubSentence by viewModel.currentEpubSentence.collectAsState()

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
            // Album art placeholder
            Card(
                modifier = Modifier
                    .size(240.dp)
                    .padding(bottom = 24.dp),
                colors = CardDefaults.cardColors(
                    containerColor = MaterialTheme.colorScheme.primaryContainer,
                ),
            ) {
                Box(
                    modifier = Modifier.fillMaxSize(),
                    contentAlignment = Alignment.Center,
                ) {
                    Text("🎧", fontSize = 64.sp)
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

            // EPUB position indicator
            Card(
                colors = CardDefaults.cardColors(
                    containerColor = MaterialTheme.colorScheme.surfaceVariant,
                ),
            ) {
                Row(
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Text("📖", fontSize = 16.sp)
                    Text(
                        "Ch. ${epubChapter + 1}, Sentence ${epubSentence + 1}",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            Spacer(Modifier.height(24.dp))

            // Progress slider
            val progress = if (durationMs > 0) positionMs.toFloat() / durationMs else 0f
            Slider(
                value = progress,
                onValueChange = { viewModel.seekTo((it * durationMs).toInt()) },
                modifier = Modifier.fillMaxWidth(),
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
                IconButton(onClick = { viewModel.skipBackward(10) }) {
                    Icon(Icons.Default.Replay10, "Rewind 10s", modifier = Modifier.size(32.dp))
                }

                FilledIconButton(
                    onClick = { viewModel.togglePlayback() },
                    modifier = Modifier.size(64.dp),
                ) {
                    Icon(
                        if (isPlaying) Icons.Default.Pause else Icons.Default.PlayArrow,
                        "Play/Pause",
                        modifier = Modifier.size(32.dp),
                    )
                }

                IconButton(onClick = { viewModel.skipForward(30) }) {
                    Icon(Icons.Default.Forward30, "Forward 30s", modifier = Modifier.size(32.dp))
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

private fun formatTime(ms: Int): String {
    val totalSeconds = ms / 1000
    val hours = totalSeconds / 3600
    val minutes = (totalSeconds % 3600) / 60
    val seconds = totalSeconds % 60
    return if (hours > 0) {
        "%d:%02d:%02d".format(hours, minutes, seconds)
    } else {
        "%d:%02d".format(minutes, seconds)
    }
}
