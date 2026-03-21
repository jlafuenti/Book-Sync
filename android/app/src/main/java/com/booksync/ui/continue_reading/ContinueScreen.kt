package com.booksync.ui.continue_reading

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import com.booksync.R

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ContinueScreen(
    onPairBookSelect: (Int) -> Unit,
    onPairAudioSelect: (Int) -> Unit,
    onEbookSelect: (Int) -> Unit,
    onAudiobookSelect: (Int) -> Unit,
    viewModel: ContinueViewModel = hiltViewModel(),
) {
    val items by viewModel.items.collectAsState()

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Continue", fontWeight = FontWeight.Bold, fontSize = 18.sp) },
                navigationIcon = {
                    Icon(
                        painter = painterResource(id = R.drawable.ic_launcher_foreground),
                        contentDescription = "Tandem",
                        tint = Color.Unspecified,
                        modifier = Modifier
                            .padding(start = 8.dp)
                            .size(40.dp)
                            .clip(RoundedCornerShape(8.dp))
                    )
                },
                windowInsets = WindowInsets(0, 0, 0, 0),
                modifier = Modifier.height(56.dp),
            )
        },
    ) { padding ->
        if (items.isEmpty()) {
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(padding)
                    .padding(32.dp),
                verticalArrangement = Arrangement.Center,
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Text("📖", fontSize = MaterialTheme.typography.displayLarge.fontSize)
                Spacer(Modifier.height(16.dp))
                Text(
                    "Nothing in progress",
                    style = MaterialTheme.typography.headlineSmall,
                    fontWeight = FontWeight.SemiBold,
                )
                Spacer(Modifier.height(8.dp))
                Text(
                    "Start reading or listening to see items here",
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
                items(items, key = { it.id }) { item ->
                    ContinueItemCard(
                        item = item,
                        onClick = {
                            when (item.mediaType) {
                                "pair" -> item.pairId?.let { onPairAudioSelect(it) }
                                "audiobook" -> item.audiobookId?.let { onAudiobookSelect(it) }
                                "ebook" -> item.ebookId?.let { onEbookSelect(it) }
                            }
                        },
                        onMarkComplete = { viewModel.markComplete(item) },
                        onResetProgress = { viewModel.resetProgress(item) },
                    )
                }
            }
        }
    }
}

@Composable
private fun ContinueItemCard(
    item: ContinueItem,
    onClick: () -> Unit,
    onMarkComplete: () -> Unit,
    onResetProgress: () -> Unit,
) {
    var showMenu by remember { mutableStateOf(false) }

    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick),
        shape = RoundedCornerShape(12.dp),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            // Type icon
            val typeEmoji = when (item.mediaType) {
                "ebook" -> "📚"
                "audiobook" -> "🎧"
                else -> "📖"
            }
            Text(
                text = typeEmoji,
                fontSize = 28.sp,
                modifier = Modifier
                    .size(48.dp)
                    .wrapContentSize(Alignment.Center)
            )

            Spacer(Modifier.width(12.dp))

            // Info
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = item.title,
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                if (!item.author.isNullOrBlank()) {
                    Text(
                        text = item.author,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
                Spacer(Modifier.height(6.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    LinearProgressIndicator(
                        progress = { (item.progressPercent / 100f).coerceIn(0f, 1f) },
                        modifier = Modifier
                            .weight(1f)
                            .height(4.dp)
                            .clip(RoundedCornerShape(2.dp)),
                    )
                    Spacer(Modifier.width(8.dp))
                    Text(
                        text = item.progressLabel,
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            // Overflow menu
            Box {
                IconButton(onClick = { showMenu = true }) {
                    Icon(Icons.Default.MoreVert, contentDescription = "Options")
                }
                DropdownMenu(
                    expanded = showMenu,
                    onDismissRequest = { showMenu = false },
                ) {
                    DropdownMenuItem(
                        text = { Text("Mark Complete") },
                        onClick = { showMenu = false; onMarkComplete() },
                    )
                    DropdownMenuItem(
                        text = { Text("Reset Progress") },
                        onClick = { showMenu = false; onResetProgress() },
                    )
                }
            }
        }
    }
}
