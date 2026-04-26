package com.booksync.ui.downloaded

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.GridItemSpan
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.DownloadDone
import androidx.compose.material3.CenterAlignedTopAppBar
import androidx.compose.material3.ExperimentalMaterial3Api
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import java.io.File
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import com.booksync.BuildConfig
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
    onOpenPairDetails: (Int) -> Unit = {},
    onOpenEbookDetails: (Int) -> Unit = {},
    onOpenAudiobookDetails: (Int) -> Unit = {},
    viewModel: DownloadedViewModel = hiltViewModel(),
) {
    val colors = Tandem.colors
    val context = LocalContext.current
    val pairs       by viewModel.downloadedPairs.collectAsState(initial = emptyList())
    val ebooks      by viewModel.downloadedEbooks.collectAsState(initial = emptyList())
    val audiobooks  by viewModel.downloadedAudiobooks.collectAsState(initial = emptyList())
    val downloading by viewModel.downloadingProgress.collectAsState()

    // Overflow sheet state — one active target at a time.
    var overflow by remember { mutableStateOf<OverflowSelection?>(null) }
    overflow?.let { sel ->
        val target = when (sel) {
            is OverflowSelection.Pair     -> sel.toOverflowTarget()
            is OverflowSelection.Ebook    -> sel.toOverflowTarget()
            is OverflowSelection.Audio    -> sel.toOverflowTarget()
        }
        val actions = when (sel) {
            is OverflowSelection.Pair  -> sel.buildActions(viewModel, onPairBookSelect, onPairAudioSelect, onOpenPairDetails)
            is OverflowSelection.Ebook -> sel.buildActions(viewModel, onEbookSelect, onOpenEbookDetails)
            is OverflowSelection.Audio -> sel.buildActions(viewModel, onAudiobookSelect, onOpenAudiobookDetails)
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
            if (pairs.isNotEmpty()) {
                item(span = { GridItemSpan(maxLineSpan) }) { SectionHeader("Matched Sets", pairs.size) }
                items(pairs, key = { "pair_${it.id}" }) { pair ->
                    val coverModel = remember(pair.audiobookId, pair.audiobookCoverPath) {
                        val localFile = File(context.filesDir, "covers/${pair.audiobookId}.jpg")
                        when {
                            localFile.exists() -> localFile
                            pair.audiobookCoverPath != null ->
                                "${BuildConfig.SERVER_BASE_URL}${pair.audiobookCoverPath}"
                            else -> null
                        }
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
                    val coverModel = remember(audio.id, audio.coverFilename) {
                        val localFile = File(context.filesDir, "covers/${audio.id}.jpg")
                        when {
                            localFile.exists() -> localFile
                            audio.coverFilename != null ->
                                "${BuildConfig.SERVER_BASE_URL}${audio.coverFilename}"
                            else -> null
                        }
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
            // Server-side transcription state — "synced" means the sync map exists
            // on the server (regardless of whether we've downloaded it locally).
            isTranscribed = pair.status == "synced",
            isQueuedOrTranscribing = pair.status == "transcribing",
            isComplete = false,
        )

        fun buildActions(
            vm: DownloadedViewModel,
            onReadClick: (Int) -> Unit,
            onListenClick: (Int) -> Unit,
            onOpenDetails: (Int) -> Unit,
        ) = OverflowActions(
            onViewDetails      = { onOpenDetails(pair.id) },
            onRead             = if (pair.ebookDownloaded)     ({ onReadClick(pair.id) })   else null,
            onListen           = if (pair.audiobookDownloaded) ({ onListenClick(pair.id) }) else null,
            // Single "Download pair" row — only appears when one side is still
            // missing. Matches the library overflow behaviour (bug 1).
            onDownloadPair     = if (!pair.ebookDownloaded || !pair.audiobookDownloaded)
                                     ({ vm.downloadAll(pair) }) else null,
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
            onOpenDetails: (Int) -> Unit,
        ) = OverflowActions(
            onViewDetails = { onOpenDetails(ebook.id) },
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
            onOpenDetails: (Int) -> Unit,
        ) = OverflowActions(
            onViewDetails     = { onOpenDetails(audio.id) },
            onListen          = { onListen(audio.id) },
            onDeleteAudiobook = { vm.deleteStandaloneAudiobook(audio) },
        )
    }
}
