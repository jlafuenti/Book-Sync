package com.booksync.ui.downloaded

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Sort
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.DownloadDone
import androidx.compose.material3.CenterAlignedTopAppBar
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
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
import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.local.entity.EBookEntity
import com.booksync.ui.components.BookCard
import com.booksync.ui.components.BookCardVariant
import com.booksync.ui.components.CardOverflowMenu
import com.booksync.ui.components.EmptyState
import com.booksync.ui.components.FilterPill
import com.booksync.ui.components.OverflowActions
import com.booksync.ui.components.OverflowTarget
import com.booksync.ui.library.LibrarySort
import com.booksync.ui.theme.Tandem

/**
 * Downloaded tab — everything you can open offline.
 *
 * Structure:
 *   - Filter pills (All / Pairs / Ebooks / Audiobooks) + sort dropdown in top bar
 *   - Unified grid respecting filter and sort
 *   - Per-card overflow sheet = same [CardOverflowMenu] used across the app
 *   - Empty state when nothing is downloaded (or nothing matches the filter)
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
    val ui          by viewModel.uiState.collectAsState()
    val items       by viewModel.items.collectAsState()
    val downloading by viewModel.downloadingProgress.collectAsState()

    var sortOpen by remember { mutableStateOf(false) }

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
                actions = {
                    Box {
                        IconButton(onClick = { sortOpen = true }) {
                            Icon(Icons.AutoMirrored.Filled.Sort, contentDescription = "Sort", tint = colors.textPrimary)
                        }
                        DropdownMenu(expanded = sortOpen, onDismissRequest = { sortOpen = false }) {
                            LibrarySort.values().forEach { sort ->
                                DropdownMenuItem(
                                    text = { Text(sort.label) },
                                    trailingIcon = {
                                        if (ui.sort == sort) {
                                            Icon(Icons.Default.Check, contentDescription = null, tint = colors.accent)
                                        }
                                    },
                                    onClick = {
                                        sortOpen = false
                                        viewModel.setSort(sort)
                                    },
                                )
                            }
                        }
                    }
                },
                colors = TopAppBarDefaults.centerAlignedTopAppBarColors(
                    containerColor = colors.bgSecondary,
                    titleContentColor = colors.textPrimary,
                ),
            )
        },
        containerColor = colors.bgPrimary,
    ) { padding ->
        Column(modifier = Modifier.padding(padding).fillMaxSize()) {
            // Filter pills
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 12.dp, vertical = 6.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                FilterPill("All",        ui.filter == DownloadedFilter.ALL,        onClick = { viewModel.setFilter(DownloadedFilter.ALL) })
                FilterPill("Pairs",      ui.filter == DownloadedFilter.PAIRS,      onClick = { viewModel.setFilter(DownloadedFilter.PAIRS) })
                FilterPill("Ebooks",     ui.filter == DownloadedFilter.EBOOKS,     onClick = { viewModel.setFilter(DownloadedFilter.EBOOKS) })
                FilterPill("Audiobooks", ui.filter == DownloadedFilter.AUDIOBOOKS, onClick = { viewModel.setFilter(DownloadedFilter.AUDIOBOOKS) })
            }

            if (items.isEmpty()) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    val (title, subtitle) = when (ui.filter) {
                        DownloadedFilter.ALL ->
                            "Nothing downloaded yet" to "Download books from the Library tab to read or listen offline."
                        DownloadedFilter.PAIRS      -> "No pairs downloaded"      to "Download a matched set from the Library."
                        DownloadedFilter.EBOOKS     -> "No ebooks downloaded"     to "Download an ebook from the Library."
                        DownloadedFilter.AUDIOBOOKS -> "No audiobooks downloaded" to "Download an audiobook from the Library."
                    }
                    EmptyState(icon = Icons.Default.DownloadDone, title = title, subtitle = subtitle)
                }
            } else {
                LazyVerticalGrid(
                    columns = GridCells.Adaptive(minSize = 140.dp),
                    modifier = Modifier.fillMaxSize(),
                    contentPadding = PaddingValues(16.dp),
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    items(items, key = { it.key }) { item ->
                        when {
                            item.pair != null -> {
                                val pair = item.pair
                                val coverModel = remember(pair.audiobookId, pair.audiobookCoverPath) {
                                    val localFile = File(context.filesDir, "covers/${pair.audiobookId}.jpg")
                                    when {
                                        localFile.exists() -> localFile
                                        pair.audiobookCoverPath != null ->
                                            "${viewModel.serverUrl.trimEnd('/')}${pair.audiobookCoverPath}"
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
                                        series = pair.ebookSeries,
                                        seriesIndex = pair.ebookSeriesIndex,
                                    ),
                                    onClick = {
                                        if (pair.ebookDownloaded) onPairBookSelect(pair.id)
                                        else onPairAudioSelect(pair.id)
                                    },
                                    onOverflow = { overflow = OverflowSelection.Pair(pair) },
                                    downloadPercent = downloading[pair.id],
                                )
                            }
                            item.ebook != null -> {
                                val ebook = item.ebook
                                BookCard(
                                    variant = BookCardVariant.SingleMedia(
                                        id = ebook.id,
                                        kind = BookCardVariant.SingleMedia.MediaKind.EBOOK,
                                        title = ebook.title,
                                        author = ebook.author,
                                        isDownloaded = true,
                                        series = ebook.series,
                                        seriesIndex = ebook.seriesIndex,
                                    ),
                                    onClick = { onEbookSelect(ebook.id) },
                                    onOverflow = { overflow = OverflowSelection.Ebook(ebook) },
                                )
                            }
                            item.audiobook != null -> {
                                val audio = item.audiobook
                                val coverModel = remember(audio.id, audio.coverFilename) {
                                    val localFile = File(context.filesDir, "covers/${audio.id}.jpg")
                                    when {
                                        localFile.exists() -> localFile
                                        audio.coverFilename != null ->
                                            "${viewModel.serverUrl.trimEnd('/')}${audio.coverFilename}"
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
                                        series = audio.series,
                                        seriesIndex = audio.seriesIndex,
                                    ),
                                    onClick = { onAudiobookSelect(audio.id) },
                                    onOverflow = { overflow = OverflowSelection.Audio(audio) },
                                )
                            }
                        }
                    }
                }
            }
        }
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
