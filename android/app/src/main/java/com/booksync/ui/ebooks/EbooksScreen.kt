package com.booksync.ui.ebooks

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.MoreVert
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
import com.booksync.data.local.entity.EBookEntity
import java.io.File

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun EbooksScreen(
    onBookSelect: (Int) -> Unit,
    onSearchClick: () -> Unit,
    viewModel: EbooksViewModel = hiltViewModel(),
) {
    val ebooks by viewModel.ebooks.collectAsState(initial = emptyList())
    val refreshing by viewModel.refreshing.collectAsState()
    val downloading by viewModel.downloading.collectAsState()

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Ebooks", fontWeight = FontWeight.Bold) },
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
        if (ebooks.isEmpty() && !refreshing) {
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
                    "No ebooks yet",
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

                items(ebooks) { ebook ->
                    EbookCard(
                        ebook = ebook,
                        isDownloading = ebook.id in downloading,
                        onDownload = { viewModel.downloadEbook(ebook) },
                        onReadClick = { onBookSelect(ebook.id) },
                    )
                }
            }
        }
    }
}

@Composable
fun EbookCard(
    ebook: EBookEntity,
    isDownloading: Boolean,
    onDownload: () -> Unit,
    onReadClick: () -> Unit,
) {
    ElevatedCard(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(enabled = ebook.isDownloaded) {
                onReadClick()
            }
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Text(
                text = ebook.title,
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.Bold,
            )
            val authorText = ebook.author ?: "Unknown Author"
            val seriesText = ebook.series?.let { "$it #${ebook.seriesIndex ?: "?"} - " } ?: ""
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
                    onClick = { },
                    label = { Text(ebook.format.uppercase()) },
                    leadingIcon = { Text("📚") }
                )
                
                // Actions
                if (ebook.isDownloaded) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(
                            Icons.Default.CheckCircle,
                            contentDescription = "Downloaded",
                            tint = MaterialTheme.colorScheme.primary,
                            modifier = Modifier.size(20.dp)
                        )
                        Spacer(Modifier.width(8.dp))
                        Button(onClick = onReadClick) {
                            Text("Read")
                        }
                    }
                } else if (isDownloading) {
                    CircularProgressIndicator(modifier = Modifier.size(24.dp))
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
