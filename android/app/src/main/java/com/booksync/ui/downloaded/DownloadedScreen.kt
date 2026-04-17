package com.booksync.ui.downloaded

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.GridItemSpan
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.DownloadDone
import androidx.compose.material.icons.filled.Storage
import androidx.compose.material3.CenterAlignedTopAppBar
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import java.io.File
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.local.entity.EBookEntity
import com.booksync.ui.components.BookCard
import com.booksync.ui.components.BookCardVariant
import com.booksync.ui.components.CardOverflowMenu
import com.booksync.ui.components.EmptyState
import com.booksync.ui.components.OverflowActions
import com.booksync.ui.components.OverflowTarget
import com.booksync.ui.theme.Tandem

/**
 * Downloaded tab — everything you can open offline.
 *
 * Structure:
 *   - Storage summary card at the top
 *   - Three sectioned grids: Matched Sets → Standalone Ebooks → Standalone Audiobooks
 *   - Per-card overflow sheet = same [CardOverflowMenu] used across the app
 *   - Empty state when nothing is downloaded
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DownloadedScreen(
    onPairBookSelect: (Int) -> Unit,
    onPairAudioSelect: (Int) -> Unit,
    onEbookSelect: (Int) -> Unit,
    onAudiobookSelect: (Int) -> Unit,
    viewModel: DownloadedViewModel = hiltViewModel(),
) {
    val colors = Tandem.colors
    val context = LocalContext.current
    val pairs       by viewModel.downloadedPairs.collectAsState(initial = emptyList())
    val ebooks      by viewModel.downloadedEbooks.collectAsState(initial = emptyList())
    val audiobooks  by viewModel.downloadedAudiobooks.collectAsState(initial = emptyList())
    val downloading by viewModel.downloadingProgress.collectAsState()
    val storage     by viewModel.storage.collectAsState()

    // Overflow sheet state — one active target at a time.
    var overflow by remember { mutableStateOf<OverflowSelection?>(null) }
    overflow?.let { sel ->
        val target = when (sel) {
            is OverflowSelection.Pair     -> sel.toOverflowTarget()
            is OverflowSelection.Ebook    -> sel.toOverflowTarget()
            is OverflowSelection.Audio    -> sel.toOverflowTarget()
        }
        val actions = when (sel) {
            is OverflowSelection.Pair  -> sel.buildActions(viewModel, onPairBookSelect, onPairAudioSelect)
            is OverflowSelection.Ebook -> sel.buildActions(viewModel, onEbookSelect)
            is OverflowSelection.Audio -> sel.buildActions(viewModel, onAudiobookSelect)
        }
        CardOverflowMenu(
            target = target,
            actions = actions,
            onDismiss = { overflow = null },
        )
    }

    Scaffold(
        topBar = {
            CenterAlignedTopAppBar(
                title = {
                    Text(
                        "Downloaded",
                        color = colors.textPrimary,
                        fontSize = 18.sp,
                        fontWeight = FontWeight.SemiBold,
                    )
                },
                colors = TopAppBarDefaults.centerAlignedTopAppBarColors(
                    containerColor = colors.bgSecondary,
                    titleContentColor = colors.textPrimary,
                ),
            )
        },
        containerColor = colors.bgPrimary,
    ) { padding ->
        if (pairs.isEmpty() && ebooks.isEmpty() && audiobooks.isEmpty()) {
            Box(Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.Center) {
                EmptyState(
                    icon = Icons.Default.DownloadDone,
                    title = "Nothing downloaded yet",
                    subtitle = "Download books from the Library tab to read or listen offline.",
                )
            }
            return@Scaffold
        }

        LazyVerticalGrid(
            columns = GridCells.Adaptive(minSize = 140.dp),
            modifier = Modifier
                .padding(padding)
                .fillMaxSize(),
            contentPadding = PaddingValues(16.dp),
            horizontalArrangement = Arrangement.spacedBy(12.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            // Storage summary spans full width
            item(span = { GridItemSpan(maxLineSpan) }) {
                StorageCard(storage)
            }

            if (pairs.isNotEmpty()) {
                item(span = { GridItemSpan(maxLineSpan) }) { SectionHeader("Matched Sets", pairs.size) }
                items(pairs, key = { "pair_${it.id}" }) { pair ->
                    val coverModel = remember(pair.audiobookId) {
                        File(context.filesDir, "covers/${pair.audiobookId}.jpg")
                    }
                    BookCard(
                        variant = BookCardVariant.Pair(
                            id = pair.id,
                            title = pair.ebookTitle,
                            author = pair.ebookAuthor ?: pair.audiobookAuthor,
                            coverImageModel = coverModel,
                            hasEbookDownloaded = pair.ebookDownloaded,
                            hasAudiobookDownloaded = pair.audiobookDownloaded,
                        ),
                        onClick = {
                            if (pair.ebookDownloaded) onPairBookSelect(pair.id)
                            else onPairAudioSelect(pair.id)
                        },
                        onOverflow = { overflow = OverflowSelection.Pair(pair) },
                        downloadPercent = downloading[pair.id],
                    )
                }
            }

            if (ebooks.isNotEmpty()) {
                item(span = { GridItemSpan(maxLineSpan) }) { SectionHeader("Standalone Ebooks", ebooks.size) }
                items(ebooks, key = { "ebook_${it.id}" }) { ebook ->
                    BookCard(
                        variant = BookCardVariant.SingleMedia(
                            id = ebook.id,
                            kind = BookCardVariant.SingleMedia.MediaKind.EBOOK,
                            title = ebook.title,
                            author = ebook.author,
                            isDownloaded = true,
                            // No cover model for standalone ebooks
                        ),
                        onClick = { onEbookSelect(ebook.id) },
                        onOverflow = { overflow = OverflowSelection.Ebook(ebook) },
                    )
                }
            }

            if (audiobooks.isNotEmpty()) {
                item(span = { GridItemSpan(maxLineSpan) }) { SectionHeader("Standalone Audiobooks", audiobooks.size) }
                items(audiobooks, key = { "audio_${it.id}" }) { audio ->
                    val coverModel = remember(audio.id) {
                        File(context.filesDir, "covers/${audio.id}.jpg")
                    }
                    BookCard(
                        variant = BookCardVariant.SingleMedia(
                            id = audio.id,
                            kind = BookCardVariant.SingleMedia.MediaKind.AUDIOBOOK,
                            title = audio.title,
                            author = audio.author,
                            coverImageModel = coverModel,
                            isDownloaded = true,
                        ),
                        onClick = { onAudiobookSelect(audio.id) },
                        onOverflow = { overflow = OverflowSelection.Audio(audio) },
                    )
                }
            }
        }
    }
}

// --------------------------------------------------------------------------
// Storage summary
// --------------------------------------------------------------------------

@Composable
private fun StorageCard(storage: StorageUsage) {
    val colors = Tandem.colors
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(Tandem.shapes.card)
            .background(colors.bgCard)
            .padding(16.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(
                modifier = Modifier
                    .size(40.dp)
                    .clip(RoundedCornerShape(10.dp))
                    .background(colors.accent.copy(alpha = 0.18f)),
                contentAlignment = Alignment.Center,
            ) {
                Icon(
                    Icons.Default.Storage,
                    contentDescription = null,
                    tint = colors.accent,
                    modifier = Modifier.size(20.dp),
                )
            }
            Spacer(Modifier.width(12.dp))
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    "Storage",
                    color = colors.textSecondary,
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Medium,
                )
                Text(
                    "${formatBytes(storage.usedBytes)} used of ${formatBytes(storage.totalBytes)}",
                    color = colors.textPrimary,
                    fontSize = 15.sp,
                    fontWeight = FontWeight.SemiBold,
                )
            }
            Text(
                "${formatBytes(storage.freeBytes)} free",
                color = colors.textMuted,
                fontSize = 12.sp,
            )
        }
        Spacer(Modifier.height(12.dp))
        LinearProgressIndicator(
            progress = { storage.usedFraction },
            modifier = Modifier
                .fillMaxWidth()
                .height(6.dp)
                .clip(RoundedCornerShape(3.dp)),
            color = colors.accent,
            trackColor = colors.bgInput,
        )
    }
}

@Composable
private fun SectionHeader(label: String, count: Int) {
    val colors = Tandem.colors
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(top = 4.dp, bottom = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            label,
            color = colors.textPrimary,
            fontSize = 15.sp,
            fontWeight = FontWeight.SemiBold,
        )
        Spacer(Modifier.width(8.dp))
        Text(
            count.toString(),
            color = colors.textMuted,
            fontSize = 12.sp,
            fontWeight = FontWeight.Medium,
        )
    }
}

private fun formatBytes(bytes: Long): String {
    if (bytes <= 0L) return "0 B"
    val units = arrayOf("B", "KB", "MB", "GB", "TB")
    var value = bytes.toDouble()
    var unit = 0
    while (value >= 1024 && unit < units.lastIndex) {
        value /= 1024
        unit++
    }
    return if (unit < 2) "${value.toInt()} ${units[unit]}"
    else String.format("%.1f %s", value, units[unit])
}

// --------------------------------------------------------------------------
// Overflow selection — small ADT that captures which card owns the open sheet,
// so the screen doesn't have to juggle separate state for each type.
// --------------------------------------------------------------------------

private sealed class OverflowSelection {
    data class Pair(val pair: BookPairEntity) : OverflowSelection() {
        fun toOverflowTarget() = OverflowTarget.Pair(
            pairId = pair.id,
            title = pair.ebookTitle,
            subtitle = pair.ebookAuthor ?: pair.audiobookAuthor,
            hasEbookDownloaded = pair.ebookDownloaded,
            hasAudiobookDownloaded = pair.audiobookDownloaded,
            isTranscribed = pair.syncMapDownloaded,
            isQueuedOrTranscribing = false,
            isComplete = false,
        )

        fun buildActions(
            vm: DownloadedViewModel,
            onReadClick: (Int) -> Unit,
            onListenClick: (Int) -> Unit,
        ) = OverflowActions(
            onRead             = if (pair.ebookDownloaded)     ({ onReadClick(pair.id) })   else null,
            onListen           = if (pair.audiobookDownloaded) ({ onListenClick(pair.id) }) else null,
            onDownloadEbook    = if (!pair.ebookDownloaded)     ({ vm.downloadEbookOnly(pair) })     else null,
            onDownloadAudiobook= if (!pair.audiobookDownloaded) ({ vm.downloadAudiobookOnly(pair) }) else null,
            onDeleteEbook      = if (pair.ebookDownloaded)     ({ vm.deleteEbook(pair) })      else null,
            onDeleteAudiobook  = if (pair.audiobookDownloaded) ({ vm.deleteAudiobook(pair) })  else null,
            onRefreshSyncData  = if (pair.syncMapDownloaded)   ({ vm.refreshSyncData(pair) })  else null,
            onMarkComplete     = { vm.markComplete(pair) },
            onResetProgress    = { vm.resetProgress(pair) },
            onUnlinkPair       = { vm.unlinkPair(pair) },
        )
    }

    data class Ebook(val ebook: EBookEntity) : OverflowSelection() {
        fun toOverflowTarget() = OverflowTarget.Ebook(
            ebookId = ebook.id,
            title = ebook.title,
            subtitle = ebook.author,
            isDownloaded = true,
            isPaired = false,
        )

        fun buildActions(
            vm: DownloadedViewModel,
            onRead: (Int) -> Unit,
        ) = OverflowActions(
            onRead        = { onRead(ebook.id) },
            onDeleteEbook = { vm.deleteStandaloneEbook(ebook) },
        )
    }

    data class Audio(val audio: AudioBookEntity) : OverflowSelection() {
        fun toOverflowTarget() = OverflowTarget.Audiobook(
            audiobookId = audio.id,
            title = audio.title,
            subtitle = audio.author,
            isDownloaded = true,
            isPaired = false,
        )

        fun buildActions(
            vm: DownloadedViewModel,
            onListen: (Int) -> Unit,
        ) = OverflowActions(
            onListen          = { onListen(audio.id) },
            onDeleteAudiobook = { vm.deleteStandaloneAudiobook(audio) },
        )
    }
}
