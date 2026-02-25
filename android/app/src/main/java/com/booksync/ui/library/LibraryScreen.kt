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

    private val _downloadingProgress = MutableStateFlow<Map<Int, String>>(emptyMap())
    val downloadingProgress = _downloadingProgress.asStateFlow()

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
            _downloadingProgress.value = _downloadingProgress.value + (pair.id to "Starting download...")
            try {
                if (!pair.ebookDownloaded) {
                    repository.downloadEbook(pair) { p ->
                        _downloadingProgress.value = _downloadingProgress.value + (pair.id to "Downloading Ebook ($p%)...")
                    }
                }
                if (!pair.audiobookDownloaded) {
                    repository.downloadAudiobook(pair) { p ->
                        _downloadingProgress.value = _downloadingProgress.value + (pair.id to "Downloading Audiobook ($p%)...")
                    }
                }
                if (!pair.syncMapDownloaded && pair.status == "synced") {
                    _downloadingProgress.value = _downloadingProgress.value + (pair.id to "Downloading Sync Data...")
                    repository.downloadSyncMap(pair.id)
                }
            } catch (_: Exception) {}
            _downloadingProgress.value = _downloadingProgress.value - pair.id
        }
    }

    fun downloadEbookOnly(pair: BookPairEntity) {
        viewModelScope.launch {
            try {
                _downloadingProgress.value = _downloadingProgress.value + (pair.id to "Starting Ebook download...")
                repository.downloadEbook(pair) { p ->
                    _downloadingProgress.value = _downloadingProgress.value + (pair.id to "Downloading Ebook ($p%)...")
                }
            } catch (_: Exception) {}
            _downloadingProgress.value = _downloadingProgress.value - pair.id
        }
    }

    fun downloadAudiobookOnly(pair: BookPairEntity) {
        viewModelScope.launch {
            try {
                _downloadingProgress.value = _downloadingProgress.value + (pair.id to "Starting Audiobook download...")
                repository.downloadAudiobook(pair) { p ->
                    _downloadingProgress.value = _downloadingProgress.value + (pair.id to "Downloading Audiobook ($p%)...")
                }
            } catch (_: Exception) {}
            _downloadingProgress.value = _downloadingProgress.value - pair.id
        }
    }

    fun deleteEbook(pair: BookPairEntity) {
        viewModelScope.launch {
            try {
                repository.deleteEbook(pair)
            } catch (_: Exception) {}
        }
    }

    fun deleteAudiobook(pair: BookPairEntity) {
        viewModelScope.launch {
            try {
                repository.deleteAudiobook(pair)
            } catch (_: Exception) {}
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun LibraryScreen(
    onBookSelect: (Int) -> Unit,
    onAudioSelect: (Int) -> Unit,
    onSearchClick: () -> Unit,
    viewModel: LibraryViewModel = hiltViewModel(),
) {
    val pairs by viewModel.pairs.collectAsState(initial = emptyList())
    val refreshing by viewModel.refreshing.collectAsState()
    val downloadingProgress by viewModel.downloadingProgress.collectAsState()

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("📖 BookSync", fontWeight = FontWeight.Bold) },
                actions = {
                    IconButton(onClick = onSearchClick) {
                        Icon(Icons.Default.Search, "Search")
                    }
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
                        downloadStatus = downloadingProgress[pair.id],
                        onDownloadAll = { viewModel.downloadAll(pair) },
                        onDownloadEbook = { viewModel.downloadEbookOnly(pair) },
                        onDownloadAudiobook = { viewModel.downloadAudiobookOnly(pair) },
                        onDeleteEbook = { viewModel.deleteEbook(pair) },
                        onDeleteAudiobook = { viewModel.deleteAudiobook(pair) },
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
    downloadStatus: String?,
    onDownloadAll: () -> Unit,
    onDownloadEbook: () -> Unit = {},
    onDownloadAudiobook: () -> Unit = {},
    onDeleteEbook: () -> Unit = {},
    onDeleteAudiobook: () -> Unit = {},
    onReadClick: () -> Unit,
    onListenClick: () -> Unit,
) {
    var showManageDialog by remember { mutableStateOf(false) }

    if (showManageDialog) {
        AlertDialog(
            onDismissRequest = { showManageDialog = false },
            title = { Text("Manage Book Pair") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (!pair.ebookDownloaded) {
                        TextButton(onClick = { onDownloadEbook(); showManageDialog = false }) { Text("Download Ebook") }
                    } else {
                        TextButton(onClick = { onDeleteEbook(); showManageDialog = false }, colors = ButtonDefaults.textButtonColors(contentColor = MaterialTheme.colorScheme.error)) { Text("Delete Local Ebook") }
                    }
                    if (!pair.audiobookDownloaded) {
                        TextButton(onClick = { onDownloadAudiobook(); showManageDialog = false }) { Text("Download Audiobook") }
                    } else {
                        TextButton(onClick = { onDeleteAudiobook(); showManageDialog = false }, colors = ButtonDefaults.textButtonColors(contentColor = MaterialTheme.colorScheme.error)) { Text("Delete Local Audiobook") }
                    }
                }
            },
            confirmButton = {
                TextButton(onClick = { showManageDialog = false }) { Text("Close") }
            }
        )
    }

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
                modifier = Modifier
                    .fillMaxWidth()
                    .clickable { showManageDialog = true }
                    .padding(vertical = 4.dp),
                color = MaterialTheme.colorScheme.primary
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
                    onClick = { showManageDialog = true },
                    label = { Text(pair.ebookFormat) },
                    leadingIcon = {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text("📚")
                            if (pair.ebookDownloaded) {
                                Spacer(Modifier.width(4.dp))
                                Icon(Icons.Default.CheckCircle, null, tint = androidx.compose.ui.graphics.Color(0xFF4CAF50), modifier = Modifier.size(16.dp))
                            }
                        }
                    },
                )
                AssistChip(
                    onClick = { showManageDialog = true },
                    label = { Text(pair.audiobookFormat) },
                    leadingIcon = {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text("🎧")
                            if (pair.audiobookDownloaded) {
                                Spacer(Modifier.width(4.dp))
                                Icon(Icons.Default.CheckCircle, null, tint = androidx.compose.ui.graphics.Color(0xFF4CAF50), modifier = Modifier.size(16.dp))
                            }
                        }
                    },
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
            if (downloadStatus != null) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    CircularProgressIndicator(modifier = Modifier.size(20.dp), strokeWidth = 2.dp)
                    Text(downloadStatus, style = MaterialTheme.typography.bodySmall)
                }
            } else if (!pair.ebookDownloaded && !pair.audiobookDownloaded) {
                FilledTonalButton(onClick = onDownloadAll) {
                    Icon(Icons.Default.Download, null, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(8.dp))
                    Text("Download All for Offline Use")
                }
            } else {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (pair.ebookDownloaded) {
                        FilledTonalButton(
                            onClick = onReadClick,
                            modifier = Modifier.weight(1f),
                        ) {
                            Text("📖 Read")
                        }
                    }
                    if (pair.audiobookDownloaded) {
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
}
