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
import androidx.compose.ui.platform.LocalContext
import android.widget.Toast
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import androidx.work.*
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.repository.BookSyncRepository
import com.booksync.worker.DownloadWorker
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class LibraryViewModel @Inject constructor(
    private val repository: BookSyncRepository,
    @param:ApplicationContext private val context: Context,
) : ViewModel() {
    private val workManager = WorkManager.getInstance(context)

    val pairs = repository.getPairsFlow()

    private val _refreshing = MutableStateFlow(false)
    val refreshing = _refreshing.asStateFlow()

    private val _downloadingProgress = MutableStateFlow<Map<Int, String>>(emptyMap())
    val downloadingProgress = _downloadingProgress.asStateFlow()

    private val _downloadError = MutableStateFlow<String?>(null)
    val downloadError = _downloadError.asStateFlow()

    init {
        refresh()
        observeWorkManager()
    }

    private fun observeWorkManager() {
        viewModelScope.launch {
            workManager.getWorkInfosByTagFlow("download_worker").collect { workInfos ->
                val newProgress = _downloadingProgress.value.toMutableMap()
                var errorMsg: String? = null

                for (info in workInfos) {
                    val pairId = info.progress.getInt(DownloadWorker.KEY_PAIR_ID, -1)
                    val progress = info.progress.getInt(DownloadWorker.PROGRESS_KEY, 0)
                    val currentType = info.progress.getString("CURRENT")
                    val isRunning = info.state == WorkInfo.State.RUNNING

                    if (pairId != -1) {
                        if (isRunning) {
                            val typeLabel = when (currentType) {
                                "EBOOK" -> "Ebook"
                                "AUDIOBOOK" -> "Audiobook"
                                "SYNC_MAP" -> "Sync Data"
                                else -> "files"
                            }
                            if (progress >= 0) {
                                newProgress[pairId] = "Downloading $typeLabel ($progress%)..."
                            } else {
                                newProgress[pairId] = "Downloading $typeLabel..."
                            }
                        } else if (info.state.isFinished) {
                            newProgress.remove(pairId)
                            if (info.state == WorkInfo.State.FAILED) {
                                val err = info.outputData.getString(DownloadWorker.ERROR_KEY)
                                if (err != null) errorMsg = err
                            }
                        }
                    }
                }
                
                _downloadingProgress.value = newProgress
                if (errorMsg != null) {
                    _downloadError.value = errorMsg
                }
            }
        }
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

    fun clearDownloadError() {
        _downloadError.value = null
    }

    fun downloadAll(pair: BookPairEntity) {
        val request = OneTimeWorkRequestBuilder<DownloadWorker>()
            .setInputData(workDataOf(
                DownloadWorker.KEY_PAIR_ID to pair.id,
                DownloadWorker.KEY_TYPE to "ALL"
            ))
            .addTag("download_worker")
            .build()
        workManager.enqueueUniqueWork(
            "download_pair_${pair.id}",
            ExistingWorkPolicy.REPLACE,
            request
        )
        _downloadingProgress.value = _downloadingProgress.value + (pair.id to "Starting download...")
    }

    fun downloadEbookOnly(pair: BookPairEntity) {
        val request = OneTimeWorkRequestBuilder<DownloadWorker>()
            .setInputData(workDataOf(
                DownloadWorker.KEY_PAIR_ID to pair.id,
                DownloadWorker.KEY_TYPE to "EBOOK"
            ))
            .addTag("download_worker")
            .build()
        workManager.enqueueUniqueWork(
            "download_ebook_${pair.id}",
            ExistingWorkPolicy.REPLACE,
            request
        )
        _downloadingProgress.value = _downloadingProgress.value + (pair.id to "Starting Ebook download...")
    }

    fun downloadAudiobookOnly(pair: BookPairEntity) {
        val request = OneTimeWorkRequestBuilder<DownloadWorker>()
            .setInputData(workDataOf(
                DownloadWorker.KEY_PAIR_ID to pair.id,
                DownloadWorker.KEY_TYPE to "AUDIOBOOK"
            ))
            .addTag("download_worker")
            .build()
        workManager.enqueueUniqueWork(
            "download_audio_${pair.id}",
            ExistingWorkPolicy.REPLACE,
            request
        )
        _downloadingProgress.value = _downloadingProgress.value + (pair.id to "Starting Audiobook download...")
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

    fun deletePair(pair: BookPairEntity) {
        viewModelScope.launch {
            try {
                repository.deletePair(pair.id)
                refresh()
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
    onSettingsClick: () -> Unit,
    viewModel: LibraryViewModel = hiltViewModel(),
) {
    val pairs by viewModel.pairs.collectAsState(initial = emptyList())
    val refreshing by viewModel.refreshing.collectAsState()
    val downloadingProgress by viewModel.downloadingProgress.collectAsState()
    val downloadError by viewModel.downloadError.collectAsState()

    val context = LocalContext.current
    LaunchedEffect(downloadError) {
        if (downloadError != null) {
            Toast.makeText(context, "Download failed: $downloadError", Toast.LENGTH_LONG).show()
            viewModel.clearDownloadError()
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("📖 BookSync", fontWeight = FontWeight.Bold) },
                actions = {
                    IconButton(onClick = onSearchClick) {
                        Icon(Icons.Default.Search, "Search")
                    }
                    IconButton(onClick = onSettingsClick) {
                        Icon(Icons.Default.Settings, "Settings")
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
                        onDeletePair = { viewModel.deletePair(pair) },
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
    onDeletePair: () -> Unit = {},
    onReadClick: () -> Unit,
    onListenClick: () -> Unit,
) {
    var showManageDialog by remember { mutableStateOf(false) }
    var showUnlinkConfirm by remember { mutableStateOf(false) }

    if (showUnlinkConfirm) {
        AlertDialog(
            onDismissRequest = { showUnlinkConfirm = false },
            icon = { Icon(Icons.Default.LinkOff, null, tint = MaterialTheme.colorScheme.error) },
            title = { Text("Unlink Pair?") },
            text = {
                Text("This will unlink the ebook and audiobook. The files themselves will not be deleted — they will appear as unpaired items in their respective tabs.")
            },
            dismissButton = {
                TextButton(onClick = { showUnlinkConfirm = false }) { Text("Cancel") }
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        showUnlinkConfirm = false
                        showManageDialog = false
                        onDeletePair()
                    },
                    colors = ButtonDefaults.textButtonColors(contentColor = MaterialTheme.colorScheme.error)
                ) { Text("Unlink") }
            }
        )
    }

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
                    HorizontalDivider(modifier = Modifier.padding(vertical = 4.dp))
                    TextButton(
                        onClick = { showUnlinkConfirm = true },
                        colors = ButtonDefaults.textButtonColors(contentColor = MaterialTheme.colorScheme.error)
                    ) {
                        Icon(Icons.Default.LinkOff, null, modifier = Modifier.size(18.dp))
                        Spacer(Modifier.width(8.dp))
                        Text("Unlink Pair")
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
