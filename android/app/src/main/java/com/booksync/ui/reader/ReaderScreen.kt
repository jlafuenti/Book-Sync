package com.booksync.ui.reader

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.viewModelScope
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.local.entity.BookmarkEntity
import com.booksync.data.repository.BookSyncRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * EPUB Reader ViewModel.
 * In a full implementation, this would load the EPUB using Readium
 * and track sentence-level position.
 */
@HiltViewModel
class ReaderViewModel @Inject constructor(
    private val repository: BookSyncRepository,
    savedStateHandle: SavedStateHandle,
) : ViewModel() {
    private val pairId: Int = savedStateHandle["pairId"] ?: 0

    private val _pair = MutableStateFlow<BookPairEntity?>(null)
    val pair = _pair.asStateFlow()

    private val _bookmark = MutableStateFlow<BookmarkEntity?>(null)
    val bookmark = _bookmark.asStateFlow()

    private val _currentChapter = MutableStateFlow(0)
    val currentChapter = _currentChapter.asStateFlow()

    private val _currentSentence = MutableStateFlow(0)
    val currentSentence = _currentSentence.asStateFlow()

    init {
        viewModelScope.launch {
            repository.getPairsFlow().collect { pairs ->
                _pair.value = pairs.find { it.id == pairId }
            }
        }
        viewModelScope.launch {
            repository.getBookmarkFlow(pairId).collect { bm ->
                _bookmark.value = bm
                bm?.let {
                    _currentChapter.value = it.epubChapter ?: 0
                    _currentSentence.value = it.epubSentenceIndex ?: 0
                }
            }
        }
        viewModelScope.launch {
            repository.refreshBookmark(pairId)
        }
    }

    fun updatePosition(chapter: Int, sentenceIndex: Int) {
        _currentChapter.value = chapter
        _currentSentence.value = sentenceIndex
        viewModelScope.launch {
            repository.updateBookmark(
                pairId = pairId,
                source = "ebook",
                epubChapter = chapter,
                epubSentenceIndex = sentenceIndex,
            )
        }
    }

    fun nextChapter() {
        val next = _currentChapter.value + 1
        updatePosition(next, 0)
    }

    fun prevChapter() {
        val prev = maxOf(0, _currentChapter.value - 1)
        updatePosition(prev, 0)
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ReaderScreen(
    pairId: Int,
    onBack: () -> Unit,
    onSwitchToAudio: () -> Unit,
    viewModel: ReaderViewModel = hiltViewModel(),
) {
    val pair by viewModel.pair.collectAsState()
    val bookmark by viewModel.bookmark.collectAsState()
    val currentChapter by viewModel.currentChapter.collectAsState()
    val currentSentence by viewModel.currentSentence.collectAsState()

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        text = pair?.ebookTitle ?: "Reader",
                        maxLines = 1,
                    )
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back")
                    }
                },
                actions = {
                    // Switch to audio button
                    FilledTonalIconButton(onClick = onSwitchToAudio) {
                        Icon(Icons.Default.Headphones, "Switch to Audio")
                    }
                },
            )
        },
        bottomBar = {
            BottomAppBar {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    IconButton(onClick = { viewModel.prevChapter() }) {
                        Icon(Icons.Default.SkipPrevious, "Previous Chapter")
                    }
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text(
                            "Chapter ${currentChapter + 1}",
                            style = MaterialTheme.typography.titleSmall,
                            fontWeight = FontWeight.SemiBold,
                        )
                        Text(
                            "Sentence ${currentSentence + 1}",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    IconButton(onClick = { viewModel.nextChapter() }) {
                        Icon(Icons.Default.SkipNext, "Next Chapter")
                    }
                }
            }
        },
    ) { padding ->
        // Reader content area
        // In full implementation, this embeds a Readium navigator
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(24.dp)
                .verticalScroll(rememberScrollState()),
        ) {
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(
                    containerColor = MaterialTheme.colorScheme.surfaceVariant,
                ),
            ) {
                Column(modifier = Modifier.padding(20.dp)) {
                    Text(
                        "📖 EPUB Reader",
                        style = MaterialTheme.typography.titleLarge,
                        fontWeight = FontWeight.Bold,
                    )
                    Spacer(Modifier.height(12.dp))
                    Text(
                        "This panel will render the EPUB content using the Readium SDK. " +
                        "The reader tracks your position at the sentence level and syncs it " +
                        "with the audiobook timeline.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        lineHeight = 24.sp,
                    )
                    Spacer(Modifier.height(16.dp))
                    Text(
                        "Current Position:",
                        style = MaterialTheme.typography.labelLarge,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Text(
                        "Chapter ${currentChapter + 1}, Sentence ${currentSentence + 1}",
                        style = MaterialTheme.typography.bodyLarge,
                        color = MaterialTheme.colorScheme.primary,
                    )
                }
            }

            Spacer(Modifier.height(16.dp))

            // Switch to audio CTA
            ElevatedButton(
                onClick = onSwitchToAudio,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Icon(Icons.Default.Headphones, null)
                Spacer(Modifier.width(8.dp))
                Text("Switch to Audiobook")
            }
            Text(
                "The audiobook will start 10 seconds before your current reading position.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 4.dp),
            )
        }
    }
}
