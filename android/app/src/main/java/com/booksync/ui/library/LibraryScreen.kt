package com.booksync.ui.library

import android.widget.Toast
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.DoneAll
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Sort
import androidx.compose.material3.CenterAlignedTopAppBar
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
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
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import com.booksync.BuildConfig
import com.booksync.ui.components.BadgeStatus
import com.booksync.ui.components.BookCard
import com.booksync.ui.components.BookCardVariant
import com.booksync.ui.components.CardOverflowMenu
import com.booksync.ui.components.EmptyState
import com.booksync.ui.components.FilterPill
import com.booksync.ui.components.OverflowActions
import com.booksync.ui.components.OverflowTarget
import com.booksync.ui.theme.Tandem

/**
 * Unified Library — one grid, five filter pills, two toggles, optional series grouping.
 *
 * Deep-link args (e.g. from Home "See all") are applied once on first composition via
 * [LibraryViewModel.applyDeepLink].
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun LibraryScreen(
    onBookSelect: (Int) -> Unit,
    onAudioSelect: (Int) -> Unit,
    onSearchClick: () -> Unit,
    onSettingsClick: () -> Unit,    // kept for back-compat, unused by new nav
    initialFilter: LibraryFilter? = null,
    initialSeries: String? = null,
    initialSort: LibrarySort? = null,
    initialGroupBySeries: Boolean? = null,
    viewModel: LibraryViewModel = hiltViewModel(),
) {
    val colors = Tandem.colors
    val context = LocalContext.current

    // Apply deep-link args once
    LaunchedEffect(initialFilter, initialSeries, initialSort, initialGroupBySeries) {
        viewModel.applyDeepLink(initialFilter, initialSeries, initialSort, initialGroupBySeries)
    }

    val ui           by viewModel.uiState.collectAsState()
    val items        by viewModel.items.collectAsState()
    val seriesStacks by viewModel.seriesStacks.collectAsState()
    val newCount     by viewModel.newItemsCount.collectAsState()
    val refreshing   by viewModel.refreshing.collectAsState()
    val refreshMsg   by viewModel.refreshMessage.collectAsState()
    val downloadErr       by viewModel.downloadError.collectAsState()
    val downloading       by viewModel.downloadingProgress.collectAsState()
    val txMessage         by viewModel.transcriptionMessage.collectAsState()
    val activeTxPairIds   by viewModel.activeTranscribingPairIds.collectAsState()

    val snackbar = remember { SnackbarHostState() }

    LaunchedEffect(refreshMsg) {
        refreshMsg?.let {
            snackbar.showSnackbar(it)
            viewModel.clearRefreshMessage()
        }
    }
    LaunchedEffect(txMessage) {
        txMessage?.let {
            snackbar.showSnackbar(it)
            viewModel.clearTranscriptionMessage()
        }
    }
    LaunchedEffect(downloadErr) {
        downloadErr?.let {
            Toast.makeText(context, "Download error: $it", Toast.LENGTH_LONG).show()
            viewModel.clearDownloadError()
        }
    }

    // Overflow bottom sheet state
    var overflowTarget by remember { mutableStateOf<OverflowTarget?>(null) }

    Scaffold(
        topBar = {
            LibraryTopBar(
                uiState = ui,
                onSearchClick = onSearchClick,
                onRefreshClick = { viewModel.refresh() },
                onSortChange = { viewModel.setSort(it) },
                onClearSeries = { viewModel.clearSeriesFilter() },
            )
        },
        snackbarHost = { SnackbarHost(snackbar) },
        containerColor = colors.bgPrimary,
    ) { padding ->
        Column(modifier = Modifier.padding(padding).fillMaxSize()) {
            if (refreshing) {
                LinearProgressIndicator(
                    modifier = Modifier.fillMaxWidth().height(2.dp),
                    color = colors.accent,
                    trackColor = colors.border,
                )
            }

            // Filter pills
            FilterPillRow(
                active = ui.filter,
                newCount = newCount,
                onPick = { viewModel.setFilter(it) },
            )

            // Toggles
            ToggleRow(
                transcribedOnly = ui.transcribedOnly,
                groupBySeries = ui.groupBySeries,
                onTranscribedToggle = { viewModel.setTranscribedOnly(it) },
                onGroupBySeriesToggle = { viewModel.setGroupBySeries(it) },
            )

            // Acknowledge all (NEW filter only)
            if (ui.filter == LibraryFilter.NEW && items.isNotEmpty()) {
                AcknowledgeAllBar(
                    count = items.size,
                    onClick = { viewModel.acknowledgeAllNew() },
                )
            }

            // Body
            when {
                items.isEmpty() && seriesStacks.isEmpty() && !refreshing -> {
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        EmptyState(
                            icon = Icons.AutoMirrored.Filled.ArrowBack, // placeholder; purposely generic
                            title = when (ui.filter) {
                                LibraryFilter.NEW -> "Nothing new"
                                else              -> "No items"
                            },
                            subtitle = when (ui.filter) {
                                LibraryFilter.NEW -> "You're all caught up."
                                else              -> "Pull down to refresh from the server."
                            },
                            actionLabel = "Refresh",
                            onAction = { viewModel.refresh() },
                        )
                    }
                }
                ui.groupBySeries -> SeriesGrid(
                    stacks = seriesStacks,
                    onStackClick = { stack -> viewModel.drillIntoSeries(stack.seriesName) },
                )
                else -> ItemGrid(
                    items = items,
                    downloadingPercent = downloading,
                    onItemClick = { item -> openItem(item, onBookSelect, onAudioSelect) },
                    onItemOverflow = { item -> overflowTarget = item.toOverflowTarget(activeTxPairIds) },
                )
            }
        }

        // Overflow sheet
        val target = overflowTarget
        if (target != null) {
            CardOverflowMenu(
                target = target,
                actions = buildOverflowActions(target, viewModel, onBookSelect, onAudioSelect),
                onDismiss = { overflowTarget = null },
            )
        }
    }
}

// ============================================================================
// Top bar
// ============================================================================

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun LibraryTopBar(
    uiState: LibraryUiState,
    onSearchClick: () -> Unit,
    onRefreshClick: () -> Unit,
    onSortChange: (LibrarySort) -> Unit,
    onClearSeries: () -> Unit,
) {
    val colors = Tandem.colors
    var sortOpen by remember { mutableStateOf(false) }

    CenterAlignedTopAppBar(
        title = {
            if (uiState.seriesFilter != null) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    IconButton(onClick = onClearSeries, modifier = Modifier.size(28.dp)) {
                        Icon(
                            Icons.AutoMirrored.Filled.ArrowBack,
                            contentDescription = "Back to all series",
                            tint = colors.textPrimary,
                            modifier = Modifier.size(18.dp),
                        )
                    }
                    Spacer(Modifier.width(6.dp))
                    Text(
                        text = uiState.seriesFilter,
                        color = colors.textPrimary,
                        fontSize = 16.sp,
                        fontWeight = FontWeight.SemiBold,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            } else {
                Text("Library", color = colors.textPrimary, fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
            }
        },
        actions = {
            IconButton(onClick = onSearchClick) {
                Icon(Icons.Default.Search, contentDescription = "Search", tint = colors.textPrimary)
            }
            Box {
                IconButton(onClick = { sortOpen = true }) {
                    Icon(Icons.Default.Sort, contentDescription = "Sort", tint = colors.textPrimary)
                }
                DropdownMenu(expanded = sortOpen, onDismissRequest = { sortOpen = false }) {
                    LibrarySort.values().forEach { sort ->
                        DropdownMenuItem(
                            text = { Text(sort.label) },
                            trailingIcon = {
                                if (uiState.sort == sort) {
                                    Icon(Icons.Default.Check, contentDescription = null, tint = colors.accent)
                                }
                            },
                            onClick = {
                                sortOpen = false
                                onSortChange(sort)
                            },
                        )
                    }
                }
            }
            IconButton(onClick = onRefreshClick) {
                Icon(Icons.Default.Refresh, contentDescription = "Refresh", tint = colors.textPrimary)
            }
        },
    )
}

// ============================================================================
// Filter pills + toggles
// ============================================================================

@Composable
private fun FilterPillRow(
    active: LibraryFilter,
    newCount: Int,
    onPick: (LibraryFilter) -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 6.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        FilterPill("All",        active == LibraryFilter.ALL,        onClick = { onPick(LibraryFilter.ALL) })
        FilterPill("Pairs",      active == LibraryFilter.PAIRS,      onClick = { onPick(LibraryFilter.PAIRS) })
        FilterPill("Ebooks",     active == LibraryFilter.EBOOKS,     onClick = { onPick(LibraryFilter.EBOOKS) })
        FilterPill("Audiobooks", active == LibraryFilter.AUDIOBOOKS, onClick = { onPick(LibraryFilter.AUDIOBOOKS) })
        FilterPill(
            label = "New",
            selected = active == LibraryFilter.NEW,
            onClick = { onPick(LibraryFilter.NEW) },
            count = newCount.takeIf { it > 0 },
        )
    }
}

@Composable
private fun ToggleRow(
    transcribedOnly: Boolean,
    groupBySeries: Boolean,
    onTranscribedToggle: (Boolean) -> Unit,
    onGroupBySeriesToggle: (Boolean) -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        ToggleChip(
            label = "Transcribed only",
            checked = transcribedOnly,
            onCheckedChange = onTranscribedToggle,
        )
        Spacer(Modifier.width(12.dp))
        ToggleChip(
            label = "Group by series",
            checked = groupBySeries,
            onCheckedChange = onGroupBySeriesToggle,
        )
    }
}

@Composable
private fun ToggleChip(label: String, checked: Boolean, onCheckedChange: (Boolean) -> Unit) {
    val colors = Tandem.colors
    Row(verticalAlignment = Alignment.CenterVertically) {
        Switch(
            checked = checked,
            onCheckedChange = onCheckedChange,
            colors = SwitchDefaults.colors(
                checkedThumbColor = colors.accent,
                checkedTrackColor = colors.accentLight,
                uncheckedThumbColor = colors.textMuted,
                uncheckedTrackColor = colors.bgInput,
                uncheckedBorderColor = colors.border,
            ),
        )
        Spacer(Modifier.width(6.dp))
        Text(label, color = colors.textSecondary, fontSize = 12.sp)
    }
}

@Composable
private fun AcknowledgeAllBar(count: Int, onClick: () -> Unit) {
    val colors = Tandem.colors
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 6.dp)
            .clip(Tandem.shapes.button)
            .background(colors.accentLight)
            .clickable(onClick = onClick)
            .padding(horizontal = 12.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(
            Icons.Default.DoneAll,
            contentDescription = null,
            tint = colors.accent,
            modifier = Modifier.size(16.dp),
        )
        Spacer(Modifier.width(8.dp))
        Text(
            "Acknowledge all ($count)",
            color = colors.accent,
            fontSize = 13.sp,
            fontWeight = FontWeight.SemiBold,
        )
    }
}

// ============================================================================
// Grids
// ============================================================================

@Composable
private fun ItemGrid(
    items: List<LibraryItem>,
    downloadingPercent: Map<Int, Int>,
    onItemClick: (LibraryItem) -> Unit,
    onItemOverflow: (LibraryItem) -> Unit,
) {
    val context = LocalContext.current
    LazyVerticalGrid(
        columns = GridCells.Adaptive(minSize = 140.dp),
        contentPadding = PaddingValues(horizontal = 12.dp, vertical = 8.dp),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
        modifier = Modifier.fillMaxSize(),
    ) {
        items(items, key = { it.key }) { item ->
            // Cover: local cached file (CoverArtHelper) first → server URL as fallback.
            val audiobookId = item.pair?.audiobookId ?: item.audiobook?.id
            val coverPath   = item.pair?.audiobookCoverPath ?: item.audiobook?.coverFilename
            val coverModel = remember(audiobookId, coverPath) {
                val localFile = audiobookId?.let { File(context.filesDir, "covers/$it.jpg") }
                when {
                    localFile != null && localFile.exists() -> localFile
                    coverPath != null ->
                        "${BuildConfig.SERVER_BASE_URL}/api/files/covers/$coverPath"
                    else -> null
                }
            }
            val variant = item.toVariant(coverModel)
            val dlPct: Int? = item.pair?.id?.let { downloadingPercent[it] }
            BookCard(
                variant = variant,
                onClick = { onItemClick(item) },
                onOverflow = { onItemOverflow(item) },
                status = item.defaultBadge(),
                downloadPercent = dlPct,
            )
        }
    }
}

@Composable
private fun SeriesGrid(
    stacks: List<SeriesStack>,
    onStackClick: (SeriesStack) -> Unit,
) {
    LazyVerticalGrid(
        columns = GridCells.Adaptive(minSize = 160.dp),
        contentPadding = PaddingValues(horizontal = 12.dp, vertical = 8.dp),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
        modifier = Modifier.fillMaxSize(),
    ) {
        items(stacks, key = { it.seriesName }) { stack ->
            BookCard(
                variant = BookCardVariant.SeriesStack(
                    seriesName     = stack.seriesName,
                    author         = stack.author,
                    itemCount      = stack.totalCount,
                    pairCount      = stack.pairCount,
                    ebookCount     = stack.ebookCount,
                    audiobookCount = stack.audiobookCount,
                ),
                onClick = { onStackClick(stack) },
                onOverflow = { /* series stacks have no overflow actions */ },
            )
        }
    }
}

// ============================================================================
// Helpers
// ============================================================================

private fun openItem(
    item: LibraryItem,
    onBookSelect: (Int) -> Unit,
    onAudioSelect: (Int) -> Unit,
) {
    val pair = item.pair
    when {
        pair != null && pair.ebookDownloaded      -> onBookSelect(pair.id)
        pair != null && pair.audiobookDownloaded  -> onAudioSelect(pair.id)
        pair != null                              -> onBookSelect(pair.id) // will show "Download ebook" launcher
        else                                      -> { /* standalone — handled in Phase D follow-up */ }
    }
}

private fun LibraryItem.toVariant(coverModel: Any? = null): BookCardVariant = when {
    pair != null       -> BookCardVariant.Pair(
        id = pair.id,
        title = pair.ebookTitle,
        author = pair.ebookAuthor ?: pair.audiobookAuthor,
        coverImageModel = coverModel,
        hasEbookDownloaded = pair.ebookDownloaded,
        hasAudiobookDownloaded = pair.audiobookDownloaded,
    )
    ebook != null      -> BookCardVariant.SingleMedia(
        id = ebook.id,
        kind = BookCardVariant.SingleMedia.MediaKind.EBOOK,
        title = ebook.title,
        author = ebook.author,
        isDownloaded = ebook.isDownloaded,
        // No cover model for standalone ebooks (no audiobook ID to extract from)
    )
    audiobook != null  -> BookCardVariant.SingleMedia(
        id = audiobook.id,
        kind = BookCardVariant.SingleMedia.MediaKind.AUDIOBOOK,
        title = audiobook.title,
        author = audiobook.author,
        coverImageModel = coverModel,
        isDownloaded = audiobook.isDownloaded,
    )
    else -> error("LibraryItem must carry at least one entity")
}

/** Status badge rule: transcribed pairs → Synced; unacknowledged → NEW; else null. */
private fun LibraryItem.defaultBadge(): BadgeStatus? = when {
    pair?.syncMapDownloaded == true -> BadgeStatus.Synced
    else -> null
}

private fun LibraryItem.toOverflowTarget(activeTxPairIds: Set<Int> = emptySet()): OverflowTarget = when {
    pair != null -> OverflowTarget.Pair(
        pairId = pair.id,
        title = pair.ebookTitle,
        subtitle = pair.ebookAuthor ?: pair.audiobookAuthor,
        hasEbookDownloaded = pair.ebookDownloaded,
        hasAudiobookDownloaded = pair.audiobookDownloaded,
        isTranscribed = pair.syncMapDownloaded,
        isQueuedOrTranscribing = pair.id in activeTxPairIds,
        isComplete = false,
    )
    ebook != null -> OverflowTarget.Ebook(
        ebookId = ebook.id,
        title = ebook.title,
        subtitle = ebook.author,
        isDownloaded = ebook.isDownloaded,
        isPaired = false,                // standalone list is already unpaired
    )
    audiobook != null -> OverflowTarget.Audiobook(
        audiobookId = audiobook.id,
        title = audiobook.title,
        subtitle = audiobook.author,
        isDownloaded = audiobook.isDownloaded,
        isPaired = false,
    )
    else -> error("toOverflowTarget called on empty LibraryItem")
}

@Composable
private fun buildOverflowActions(
    target: OverflowTarget,
    vm: LibraryViewModel,
    onBookSelect: (Int) -> Unit,
    onAudioSelect: (Int) -> Unit,
): OverflowActions {
    // Resolve the live pair from the VM so per-action state is accurate.
    val items by vm.items.collectAsState()
    val isOnline by vm.isOnline.collectAsState()
    val pair = (target as? OverflowTarget.Pair)?.pairId?.let { pid ->
        items.firstOrNull { it.pair?.id == pid }?.pair
    }

    return when (target) {
        is OverflowTarget.Pair -> OverflowActions(
            isOnline          = isOnline,
            onRead            = pair?.let { { onBookSelect(it.id) } },
            onListen          = pair?.let { { onAudioSelect(it.id) } },
            onDownloadEbook   = pair?.let { { vm.downloadEbook(it) } },
            onDownloadAudiobook = pair?.let { { vm.downloadAudiobook(it) } },
            onDeleteEbook     = pair?.let { { vm.deleteEbookOf(it) } },
            onDeleteAudiobook = pair?.let { { vm.deleteAudiobookOf(it) } },
            // Transcription: only offer "Transcribe" if not yet transcribed and not queued.
            // The OverflowTarget.Pair already carries `isTranscribed` and `isQueuedOrTranscribing`
            // so the sheet itself decides which action to render — just wire all three.
            onTranscribe           = pair?.let { { vm.addToTranscriptionQueue(it) } },
            onCancelTranscription  = pair?.let { { vm.cancelTranscription(it) } },
            onRefreshSyncData = pair?.takeIf { it.syncMapDownloaded }?.let { { vm.refreshSyncData(it) } },
            onMarkComplete    = pair?.let { { vm.markComplete(it) } },
            onResetProgress   = pair?.let { { vm.resetProgress(it) } },
            onUnlinkPair      = pair?.let { { vm.unlinkPair(it) } },
        )
        is OverflowTarget.Ebook -> OverflowActions(
            isOnline = isOnline,
            onRead          = null, // standalone ebook reader wired in Phase D.6
            onDownloadEbook = null, // download-by-standalone-id wired in Phase D.6
            onDeleteEbook   = null,
            onMarkComplete  = null,
            onResetProgress = null,
            onPairWith      = null, // pairing sheet reused from SearchScreen in Phase D.6
        )
        is OverflowTarget.Audiobook -> OverflowActions(
            isOnline = isOnline,
            onListen            = null,
            onDownloadAudiobook = null,
            onDeleteAudiobook   = null,
            onMarkComplete      = null,
            onResetProgress     = null,
            onPairWith          = null,
        )
    }
}
