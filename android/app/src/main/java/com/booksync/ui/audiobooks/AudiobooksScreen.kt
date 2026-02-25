package com.booksync.ui.audiobooks

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import com.booksync.data.local.entity.AudioBookEntity

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AudiobooksScreen(
    onAudioSelect: (Int) -> Unit,
    onSearchClick: () -> Unit,
    viewModel: AudiobooksViewModel = hiltViewModel(),
) {
    val audiobooks by viewModel.audiobooks.collectAsState(initial = emptyList())
    val refreshing by viewModel.refreshing.collectAsState()
    val downloadingProgress by viewModel.downloadingProgress.collectAsState()

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Audiobooks", fontWeight = FontWeight.Bold) },
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
        if (audiobooks.isEmpty() && !refreshing) {
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(padding)
                    .padding(32.dp),
                verticalArrangement = Arrangement.Center,
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Text("🎧", fontSize = MaterialTheme.typography.displayLarge.fontSize)
                Spacer(Modifier.height(16.dp))
                Text(
                    "No audiobooks yet",
                    style = MaterialTheme.typography.headlineSmall,
                    fontWeight = FontWeight.SemiBold,
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

                items(audiobooks) { audio ->
                    AudiobookCard(
                        audio = audio,
                        downloadStatus = downloadingProgress[audio.id],
                        onDownload = { viewModel.downloadAudiobook(audio) },
                        onDelete = { viewModel.deleteAudiobook(audio) },
                        onListenClick = { onAudioSelect(audio.id) },
                    )
                }
            }
        }
    }
}

@Composable
fun AudiobookCard(
    audio: AudioBookEntity,
    downloadStatus: String?,
    onDownload: () -> Unit,
    onDelete: () -> Unit = {},
    onListenClick: () -> Unit,
) {
    var showManageDialog by remember { mutableStateOf(false) }

    if (showManageDialog) {
        AlertDialog(
            onDismissRequest = { showManageDialog = false },
            title = { Text("Manage Audiobook") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (!audio.isDownloaded) {
                        TextButton(onClick = { onDownload(); showManageDialog = false }) { Text("Download Audiobook") }
                    } else {
                        TextButton(onClick = { onDelete(); showManageDialog = false }, colors = ButtonDefaults.textButtonColors(contentColor = MaterialTheme.colorScheme.error)) { Text("Delete Local Audiobook") }
                    }
                }
            },
            confirmButton = {
                TextButton(onClick = { showManageDialog = false }) { Text("Close") }
            }
        )
    }

    ElevatedCard(
        modifier = Modifier.fillMaxWidth()
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Text(
                text = audio.title,
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.Bold,
                modifier = Modifier
                    .fillMaxWidth()
                    .clickable { showManageDialog = true }
                    .padding(vertical = 4.dp),
                color = MaterialTheme.colorScheme.primary
            )
            val authorText = audio.author ?: "Unknown Author"
            val seriesText = audio.series?.let { "$it #${audio.seriesIndex ?: "?"} - " } ?: ""
            Text(
                text = seriesText + authorText,
                style = MaterialTheme.typography.bodyMedium,
            )

            Spacer(modifier = Modifier.height(16.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                // Formatting chip
                AssistChip(
                    onClick = { showManageDialog = true },
                    label = { Text(audio.format.uppercase()) },
                    leadingIcon = {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text("🎧")
                            if (audio.isDownloaded) {
                                Spacer(Modifier.width(4.dp))
                                Icon(Icons.Default.CheckCircle, null, tint = androidx.compose.ui.graphics.Color(0xFF4CAF50), modifier = Modifier.size(16.dp))
                            }
                        }
                    }
                )
                
                // Actions
                if (audio.isDownloaded) {
                    Button(onClick = onListenClick) {
                        Text("🎧 Listen")
                    }
                } else if (downloadStatus != null) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        CircularProgressIndicator(modifier = Modifier.size(20.dp), strokeWidth = 2.dp)
                        Spacer(Modifier.width(8.dp))
                        Text(downloadStatus, style = MaterialTheme.typography.bodySmall)
                    }
                } else {
                    OutlinedButton(onClick = onDownload) {
                        Icon(Icons.Default.Download, contentDescription = null)
                        Spacer(Modifier.width(8.dp))
                        Text("Download")
                    }
                }
            }
        }
    }
}
