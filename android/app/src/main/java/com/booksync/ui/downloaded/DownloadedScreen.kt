package com.booksync.ui.downloaded

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import com.booksync.ui.audiobooks.AudiobookCard
import com.booksync.ui.ebooks.EbookCard
import com.booksync.ui.library.BookPairCard

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DownloadedScreen(
    onPairBookSelect: (Int) -> Unit,
    onPairAudioSelect: (Int) -> Unit,
    onEbookSelect: (Int) -> Unit,
    onAudiobookSelect: (Int) -> Unit,
    viewModel: DownloadedViewModel = hiltViewModel(),
) {
    val pairs by viewModel.downloadedPairs.collectAsState(initial = emptyList())
    val ebooks by viewModel.downloadedEbooks.collectAsState(initial = emptyList())
    val audiobooks by viewModel.downloadedAudiobooks.collectAsState(initial = emptyList())
    val downloadingProgress by viewModel.downloadingProgress.collectAsState()

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Downloaded", fontWeight = FontWeight.Bold) },
            )
        },
    ) { padding ->
        if (pairs.isEmpty() && ebooks.isEmpty() && audiobooks.isEmpty()) {
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(padding)
                    .padding(32.dp),
                verticalArrangement = Arrangement.Center,
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Text("📥", fontSize = MaterialTheme.typography.displayLarge.fontSize)
                Spacer(Modifier.height(16.dp))
                Text(
                    "Nothing downloaded",
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
                if (pairs.isNotEmpty()) {
                    item {
                        Text(
                            text = "Matched Sets",
                            style = MaterialTheme.typography.titleMedium,
                            fontWeight = FontWeight.Bold,
                            color = MaterialTheme.colorScheme.primary,
                            modifier = Modifier.padding(top = 8.dp, bottom = 4.dp)
                        )
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
                            onReadClick = { onPairBookSelect(pair.id) },
                            onListenClick = { onPairAudioSelect(pair.id) },
                        )
                    }
                }

                if (ebooks.isNotEmpty()) {
                    item {
                        Text(
                            text = "Standalone Ebooks",
                            style = MaterialTheme.typography.titleMedium,
                            fontWeight = FontWeight.Bold,
                            color = MaterialTheme.colorScheme.primary,
                            modifier = Modifier.padding(top = 8.dp, bottom = 4.dp)
                        )
                    }
                    items(ebooks) { ebook ->
                        EbookCard(
                            ebook = ebook,
                            downloadStatus = downloadingProgress[ebook.id],
                            onDownload = {},
                            onDelete = { viewModel.deleteStandaloneEbook(ebook) },
                            onReadClick = { onEbookSelect(ebook.id) },
                        )
                    }
                }

                if (audiobooks.isNotEmpty()) {
                    item {
                        Text(
                            text = "Standalone Audiobooks",
                            style = MaterialTheme.typography.titleMedium,
                            fontWeight = FontWeight.Bold,
                            color = MaterialTheme.colorScheme.primary,
                            modifier = Modifier.padding(top = 8.dp, bottom = 4.dp)
                        )
                    }
                    items(audiobooks) { audio ->
                        AudiobookCard(
                            audio = audio,
                            downloadStatus = downloadingProgress[audio.id],
                            onDownload = {},
                            onDelete = { viewModel.deleteStandaloneAudiobook(audio) },
                            onListenClick = { onAudiobookSelect(audio.id) },
                        )
                    }
                }
            }
        }
    }
}
