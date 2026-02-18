package com.booksync.ui.library

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.repository.BookSyncRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class LibraryViewModel @Inject constructor(
    private val repository: BookSyncRepository,
) : ViewModel() {
    val pairs = repository.getPairsFlow()

    private val _refreshing = MutableStateFlow(false)
    val refreshing = _refreshing.asStateFlow()

    private val _downloading = MutableStateFlow<Set<Int>>(emptySet())
    val downloading = _downloading.asStateFlow()

    init {
        refresh()
    }

    fun refresh() {
        viewModelScope.launch {
            _refreshing.value = true
            try {
                repository.refreshPairs()
            } catch (_: Exception) {}
            _refreshing.value = false
        }
    }

    fun downloadAll(pair: BookPairEntity) {
        viewModelScope.launch {
            _downloading.value = _downloading.value + pair.id
            try {
                if (!pair.ebookDownloaded) repository.downloadEbook(pair)
                if (!pair.audiobookDownloaded) repository.downloadAudiobook(pair)
                if (!pair.syncMapDownloaded && pair.status == "synced") {
                    repository.downloadSyncMap(pair.id)
                }
            } catch (_: Exception) {}
            _downloading.value = _downloading.value - pair.id
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun LibraryScreen(
    onBookSelect: (Int) -> Unit,
    onAudioSelect: (Int) -> Unit,
    viewModel: LibraryViewModel = hiltViewModel(),
) {
    val pairs by viewModel.pairs.collectAsState(initial = emptyList())
    val refreshing by viewModel.refreshing.collectAsState()
    val downloading by viewModel.downloading.collectAsState()

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("📖 BookSync", fontWeight = FontWeight.Bold) },
                actions = {
                    IconButton(onClick = { viewModel.refresh() }) {
                        Icon(Icons.Default.Refresh, "Refresh")
                    }
                },
            )
        },
    ) { padding ->
        if (pairs.isEmpty() && !refreshing) {
            // Empty state
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(padding)
                    .padding(32.dp),
                verticalArrangement = Arrangement.Center,
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Text("📚", fontSize = MaterialTheme.typography.displayLarge.fontSize)
                Spacer(Modifier.height(16.dp))
                Text(
                    "No books yet",
                    style = MaterialTheme.typography.headlineSmall,
                    fontWeight = FontWeight.SemiBold,
                )
                Spacer(Modifier.height(8.dp))
                Text(
                    "Add book pairs on the web dashboard,\nthen pull to refresh here.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        } else {
            LazyColumn(
                modifier = Modifier.padding(padding),
                contentPadding = PaddingValues(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                if (refreshing) {
                    item {
                        LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
                    }
                }

                items(pairs) { pair ->
                    BookPairCard(
                        pair = pair,
                        isDownloading = pair.id in downloading,
                        onDownload = { viewModel.downloadAll(pair) },
                        onReadClick = { onBookSelect(pair.id) },
                        onListenClick = { onAudioSelect(pair.id) },
                    )
                }
            }
        }
    }
}

@Composable
fun BookPairCard(
    pair: BookPairEntity,
    isDownloading: Boolean,
    onDownload: () -> Unit,
    onReadClick: () -> Unit,
    onListenClick: () -> Unit,
) {
    val isReady = pair.ebookDownloaded && pair.audiobookDownloaded && pair.syncMapDownloaded

    ElevatedCard(
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            // Title
            Text(
                text = pair.ebookTitle,
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.Bold,
            )
            pair.ebookAuthor?.let {
                Text(
                    text = it,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            Spacer(Modifier.height(8.dp))

            // Status chips
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                AssistChip(
                    onClick = {},
                    label = { Text(pair.ebookFormat) },
                    leadingIcon = { Text("📚") },
                )
                AssistChip(
                    onClick = {},
                    label = { Text(pair.audiobookFormat) },
                    leadingIcon = { Text("🎧") },
                )
                if (pair.status == "synced") {
                    AssistChip(
                        onClick = {},
                        label = { Text("Synced") },
                        leadingIcon = { Icon(Icons.Default.Check, null, modifier = Modifier.size(16.dp)) },
                        colors = AssistChipDefaults.assistChipColors(
                            containerColor = MaterialTheme.colorScheme.primaryContainer,
                        ),
                    )
                }
            }

            Spacer(Modifier.height(8.dp))

            // Download / action bar
            if (isDownloading) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    CircularProgressIndicator(modifier = Modifier.size(20.dp), strokeWidth = 2.dp)
                    Text("Downloading...", style = MaterialTheme.typography.bodySmall)
                }
            } else if (!isReady) {
                FilledTonalButton(onClick = onDownload) {
                    Icon(Icons.Default.Download, null, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(8.dp))
                    Text("Download for Offline Use")
                }
            } else {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    FilledTonalButton(
                        onClick = onReadClick,
                        modifier = Modifier.weight(1f),
                    ) {
                        Text("📖 Read")
                    }
                    Button(
                        onClick = onListenClick,
                        modifier = Modifier.weight(1f),
                    ) {
                        Text("🎧 Listen")
                    }
                }
            }
        }
    }
}
