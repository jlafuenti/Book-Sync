package com.booksync.ui.player

import android.widget.FrameLayout
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.google.android.material.bottomsheet.BottomSheetBehavior

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun UnifiedAudioPlayer(
    viewModel: PlayerViewModel,
    bottomSheetBehavior: BottomSheetBehavior<FrameLayout>
) {
    val pair by viewModel.pair.collectAsState()
    val isPlaying by viewModel.isPlaying.collectAsState()
    val positionMs by viewModel.positionMs.collectAsState()
    val durationMs by viewModel.durationMs.collectAsState()
    val speed by viewModel.speed.collectAsState()
    val sleepTimerMinutes by viewModel.sleepTimerMinutes.collectAsState()
    val sleepTimerRemainingMs by viewModel.sleepTimerRemainingMs.collectAsState()
    val coverArtBitmap by viewModel.coverArtBitmap.collectAsState()
    val coverArt = coverArtBitmap?.asImageBitmap()

    var sheetState by remember { mutableStateOf(bottomSheetBehavior.state) }
    var showSleepTimerDialog by remember { mutableStateOf(false) }
    val isDownloaded = pair?.audiobookDownloaded == true

    // Listen to behavior changes
    DisposableEffect(bottomSheetBehavior) {
        val callback = object : BottomSheetBehavior.BottomSheetCallback() {
            override fun onStateChanged(bottomSheet: android.view.View, newState: Int) {
                sheetState = newState
            }
            override fun onSlide(bottomSheet: android.view.View, slideOffset: Float) {}
        }
        bottomSheetBehavior.addBottomSheetCallback(callback)
        onDispose { bottomSheetBehavior.removeBottomSheetCallback(callback) }
    }

    if (!isDownloaded) {
        return // Do not show the audio controls if audiobook is not downloaded
    }

    val isExpanded = sheetState == BottomSheetBehavior.STATE_EXPANDED

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

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surface)
    ) {
        // Drag handle for bottom sheet
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .padding(vertical = 8.dp)
                .clickable {
                    bottomSheetBehavior.state = if (isExpanded) {
                        BottomSheetBehavior.STATE_COLLAPSED
                    } else {
                        BottomSheetBehavior.STATE_EXPANDED
                    }
                },
            contentAlignment = Alignment.Center
        ) {
            Box(
                modifier = Modifier
                    .width(32.dp)
                    .height(4.dp)
                    .clip(MaterialTheme.shapes.small)
                    .background(MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.4f))
            )
        }

        if (isExpanded) {
            ExpandedPlayer(
                viewModel = viewModel,
                pairTitle = pair?.audiobookTitle ?: "Audiobook",
                pairAuthor = pair?.audiobookAuthor,
                isPlaying = isPlaying,
                positionMs = positionMs,
                durationMs = durationMs,
                speed = speed,
                sleepTimerMinutes = sleepTimerMinutes,
                sleepTimerRemainingMs = sleepTimerRemainingMs,
                coverArt = coverArt,
                onShowSleepTimerDialog = { showSleepTimerDialog = true },
                onCollapse = { bottomSheetBehavior.state = BottomSheetBehavior.STATE_COLLAPSED }
            )
        } else {
            MiniPlayer(
                title = pair?.audiobookTitle ?: "Audiobook",
                author = pair?.audiobookAuthor,
                isPlaying = isPlaying,
                positionMs = positionMs,
                durationMs = durationMs,
                coverArt = coverArt,
                onPlayPauseClick = { viewModel.togglePlayback() },
                onExpandClick = { bottomSheetBehavior.state = BottomSheetBehavior.STATE_EXPANDED }
            )
        }
    }
}

@Composable
fun MiniPlayer(
    title: String,
    author: String?,
    isPlaying: Boolean,
    positionMs: Long,
    durationMs: Long,
    coverArt: androidx.compose.ui.graphics.ImageBitmap?,
    onPlayPauseClick: () -> Unit,
    onExpandClick: () -> Unit
) {
    val progress = if (durationMs > 0) positionMs.toFloat() / durationMs else 0f

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clickable { onExpandClick() }
    ) {
        LinearProgressIndicator(
            progress = { progress },
            modifier = Modifier.fillMaxWidth().height(2.dp),
            color = MaterialTheme.colorScheme.primary,
            trackColor = MaterialTheme.colorScheme.surfaceVariant
        )
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            // Mini Album Art
            Card(
                modifier = Modifier.size(48.dp),
                shape = MaterialTheme.shapes.small
            ) {
                if (coverArt != null) {
                    Image(
                        bitmap = coverArt,
                        contentDescription = "Album Art",
                        modifier = Modifier.fillMaxSize(),
                        contentScale = ContentScale.Crop
                    )
                } else {
                    Box(
                        modifier = Modifier.fillMaxSize(),
                        contentAlignment = Alignment.Center
                    ) {
                        Icon(Icons.Default.MusicNote, contentDescription = null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }

            Spacer(Modifier.width(12.dp))

            // Title and Author
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = title,
                    style = MaterialTheme.typography.bodyLarge,
                    fontWeight = FontWeight.Bold,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis
                )
                if (author != null) {
                    Text(
                        text = author,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis
                    )
                }
            }

            // Play/Pause button
            IconButton(onClick = onPlayPauseClick, modifier = Modifier.size(48.dp)) {
                Icon(
                    if (isPlaying) Icons.Default.Pause else Icons.Default.PlayArrow,
                    contentDescription = "Play/Pause",
                    modifier = Modifier.size(32.dp)
                )
            }
        }
    }
}

@Composable
fun ExpandedPlayer(
    viewModel: PlayerViewModel,
    pairTitle: String,
    pairAuthor: String?,
    isPlaying: Boolean,
    positionMs: Long,
    durationMs: Long,
    speed: Float,
    sleepTimerMinutes: Int,
    sleepTimerRemainingMs: Long,
    coverArt: androidx.compose.ui.graphics.ImageBitmap?,
    onShowSleepTimerDialog: () -> Unit,
    onCollapse: () -> Unit
) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        // Album art (scalable for expansion)
        Card(
            modifier = Modifier
                .size(200.dp)
                .padding(bottom = 24.dp),
            colors = CardDefaults.cardColors(
                containerColor = MaterialTheme.colorScheme.primaryContainer,
            ),
        ) {
            if (coverArt != null) {
                Image(
                    bitmap = coverArt,
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
            text = pairTitle,
            style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.Bold,
            textAlign = TextAlign.Center,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis
        )
        if (pairAuthor != null) {
            Text(
                text = pairAuthor,
                style = MaterialTheme.typography.bodyLarge,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis
            )
        }

        Spacer(Modifier.height(24.dp))

        // Progress slider
        val progress = if (durationMs > 0) positionMs.toFloat() / durationMs else 0f
        Slider(
            value = progress,
            onValueChange = { viewModel.seekTo((it * durationMs).toLong()) },
            modifier = Modifier.fillMaxWidth()
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
                modifier = Modifier.size(64.dp)
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

        Spacer(Modifier.height(16.dp))

        // Speed + Sleep Timer row
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceEvenly,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            // Speed button
            FilledTonalButton(onClick = { viewModel.cycleSpeed() }) {
                val speedText = if (speed % 1.0f == 0f) {
                    "%.1f×".format(speed)
                } else {
                    "${speed}×"
                }
                Text(speedText, fontWeight = FontWeight.Bold)
            }

            // Sleep timer button
            FilledTonalButton(onClick = onShowSleepTimerDialog) {
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
        
        TextButton(onClick = onCollapse) {
            Icon(Icons.Default.KeyboardArrowDown, contentDescription = null)
            Spacer(Modifier.width(8.dp))
            Text("Hide Player")
        }
        Spacer(Modifier.height(8.dp))
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
