package com.booksync.ui.home

import android.content.Intent
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.LibraryBooks
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.CenterAlignedTopAppBar
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.net.toUri
import androidx.hilt.navigation.compose.hiltViewModel
import com.booksync.BuildConfig
import java.io.File
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.data.remote.coverImageUrl
import com.booksync.data.repository.PairOpenTarget
import kotlinx.coroutines.launch
import com.booksync.ui.components.BadgeStatus
import com.booksync.ui.components.BookCard
import com.booksync.ui.components.BookCardVariant
import com.booksync.ui.components.CardOverflowMenu
import com.booksync.ui.components.EmptyState
import com.booksync.ui.components.OverflowActions
import com.booksync.ui.components.OverflowTarget
import com.booksync.ui.theme.Tandem

/**
 * Home feed — four horizontal carousels.
 *
 * Sections render only when they have content. If every section is empty we show a
 * single [EmptyState] that nudges the user to open the web app (where uploads happen).
 *
 * "See all →" on each section deep-links into Library with the matching filter/sort.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HomeScreen(
    onSearchClick: () -> Unit,
    onOpenPairReader: (pairId: Int) -> Unit,
    onOpenPairPlayer: (pairId: Int) -> Unit,
    onOpenEbook: (ebookId: Int) -> Unit,
    onOpenAudiobook: (audiobookId: Int) -> Unit,
    onOpenPairDetails: (Int) -> Unit = {},
    onOpenEbookDetails: (Int) -> Unit = {},
    onOpenAudiobookDetails: (Int) -> Unit = {},
    onSeeAll: (route: HomeSeeAll) -> Unit,
    viewModel: HomeViewModel = hiltViewModel(),
) {
    val colors = Tandem.colors
    val continueItems by viewModel.continueItems.collectAsState()
    val recentlyAdded by viewModel.recentlyAdded.collectAsState()
    val newPairs by viewModel.newPairs.collectAsState()
    val queueItems by viewModel.queueItems.collectAsState()
    val activeTxPairIds by viewModel.activeTxPairIds.collectAsState()

    val allEmpty = continueItems.isEmpty() &&
        recentlyAdded.isEmpty() &&
        newPairs.isEmpty() &&
        queueItems.isEmpty()

    // Overflow sheet state — set when a card's three-dots is tapped.
    var overflowTarget by remember { mutableStateOf<OverflowTarget?>(null) }

    // Snackbar host for transcription-action feedback.
    val snackbarHostState = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()

    // Route a pair tap to whichever medium the user last used (bookmark.source).
    // Falls back to today's preference (reader) when there is no prior bookmark.
    val openPair: (Int) -> Unit = { pairId ->
        scope.launch {
            when (viewModel.resolvePairOpenTarget(pairId)) {
                PairOpenTarget.Reader, PairOpenTarget.Details -> onOpenPairReader(pairId)
                PairOpenTarget.Player -> onOpenPairPlayer(pairId)
            }
        }
        Unit
    }
    val transcriptionMessage by viewModel.transcriptionMessage.collectAsState()
    LaunchedEffect(transcriptionMessage) {
        transcriptionMessage?.let {
            snackbarHostState.showSnackbar(it)
            viewModel.clearTranscriptionMessage()
        }
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbarHostState) },
        topBar = {
            CenterAlignedTopAppBar(
                title = {
                    Text(
                        "Home",
                        color = colors.textPrimary,
                        fontSize = 18.sp,
                        fontWeight = FontWeight.SemiBold,
                    )
                },
                actions = {
                    IconButton(onClick = onSearchClick) {
                        Icon(
                            Icons.Default.Search,
                            contentDescription = "Search",
                            tint = colors.textPrimary,
                        )
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
        if (allEmpty) {
            val webAppUrl by viewModel.webAppUrl.collectAsState()
            HomeEmptyState(
                webAppUrl = webAppUrl,
                modifier = Modifier.padding(padding).fillMaxSize(),
            )
            return@Scaffold
        }

        LazyColumn(
            modifier = Modifier
                .padding(padding)
                .fillMaxSize(),
            contentPadding = PaddingValues(vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            if (continueItems.isNotEmpty()) {
                item {
                    SectionHeader(
                        title = "Continue Reading",
                        onSeeAll = { onSeeAll(HomeSeeAll.CONTINUE) },
                    )
                    ContinueRow(
                        items = continueItems,
                        serverUrl = viewModel.serverUrl,
                        onItemClick = { item ->
                            when (item.mediaType) {
                                HomeItem.MediaType.PAIR      -> item.pairId?.let(openPair)
                                HomeItem.MediaType.EBOOK     -> item.ebookId?.let(onOpenEbook)
                                HomeItem.MediaType.AUDIOBOOK -> item.audiobookId?.let(onOpenAudiobook)
                            }
                        },
                        onItemOverflow = { item ->
                            overflowTarget = item.toOverflowTarget(
                                recentlyAdded = recentlyAdded,
                                newPairs = newPairs,
                                activeTxPairIds = activeTxPairIds,
                            )
                        },
                    )
                }
            }

            if (recentlyAdded.isNotEmpty()) {
                item {
                    SectionHeader(
                        title = "Recently Added",
                        onSeeAll = { onSeeAll(HomeSeeAll.RECENTLY_ADDED) },
                    )
                    PairRow(
                        pairs = recentlyAdded,
                        serverUrl = viewModel.serverUrl,
                        showNewBadge = false,
                        onPairClick = { pair -> openPair(pair.id) },
                        onPairOverflow = { pair ->
                            overflowTarget = pair.toPairOverflowTarget(activeTxPairIds)
                        },
                    )
                }
            }

            if (newPairs.isNotEmpty()) {
                item {
                    SectionHeader(
                        title = "New Pairs",
                        onSeeAll = { onSeeAll(HomeSeeAll.NEW) },
                        // Issue #222: clears the pairs only. The Library NEW
                        // filter's "Acknowledge all" also clears every new
                        // ebook and audiobook, which is why this section needed
                        // a dismiss of its own.
                        onDismiss = { viewModel.dismissNewPairs() },
                    )
                    PairRow(
                        pairs = newPairs,
                        serverUrl = viewModel.serverUrl,
                        showNewBadge = true,
                        onPairClick = { pair -> openPair(pair.id) },
                        onPairOverflow = { pair ->
                            overflowTarget = pair.toPairOverflowTarget(activeTxPairIds)
                        },
                    )
                }
            }

            if (queueItems.isNotEmpty()) {
                item {
                    SectionHeader(
                        title = "In Queue",
                        onSeeAll = { onSeeAll(HomeSeeAll.QUEUE) },
                    )
                    QueueRow(
                        items = queueItems,
                        onItemClick = { q -> openPair(q.pairId) },
                    )
                }
            }
        }

        // Overflow sheet — shown when the user taps a card's three-dots button.
        val target = overflowTarget
        if (target != null) {
            CardOverflowMenu(
                target = target,
                actions = buildHomeOverflowActions(
                    target = target,
                    vm = viewModel,
                    onOpenPairReader = onOpenPairReader,
                    onOpenPairPlayer = onOpenPairPlayer,
                    onOpenEbook = onOpenEbook,
                    onOpenAudiobook = onOpenAudiobook,
                    onOpenPairDetails = onOpenPairDetails,
                    onOpenEbookDetails = onOpenEbookDetails,
                    onOpenAudiobookDetails = onOpenAudiobookDetails,
                ),
                onDismiss = { overflowTarget = null },
            )
        }
    }
}

/** Deep-link hint passed to the caller so it can build the matching library URL. */
enum class HomeSeeAll { CONTINUE, RECENTLY_ADDED, NEW, QUEUE }

// --------------------------------------------------------------------------
// Subcomponents
// --------------------------------------------------------------------------

/**
 * [onDismiss], when given, renders a per-section "Dismiss" next to "See all"
 * (issue #222). Only "New Pairs" uses it today.
 */
@Composable
private fun SectionHeader(title: String, onSeeAll: () -> Unit, onDismiss: (() -> Unit)? = null) {
    val colors = Tandem.colors
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = title,
            color = colors.textPrimary,
            fontSize = 16.sp,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.weight(1f),
        )
        if (onDismiss != null) {
            TextButton(onClick = onDismiss) {
                Text("Dismiss", color = colors.textSecondary, fontSize = 13.sp)
            }
        }
        TextButton(onClick = onSeeAll) {
            Text("See all", color = colors.accent, fontSize = 13.sp)
            Icon(
                Icons.Default.ChevronRight,
                contentDescription = null,
                tint = colors.accent,
                modifier = Modifier.size(16.dp),
            )
        }
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun ContinueRow(
    items: List<HomeItem>,
    serverUrl: String,
    onItemClick: (HomeItem) -> Unit,
    onItemOverflow: (HomeItem) -> Unit,
) {
    val context = LocalContext.current
    LazyRow(
        contentPadding = PaddingValues(horizontal = 16.dp),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        items(items, key = { it.id }) { item ->
            // Cover: prefer local cached file (CoverArtHelper) → fall back to server URL.
            val coverModel = remember(item.audiobookId, item.audiobookCoverPath) {
                val localFile = item.audiobookId?.let { File(context.filesDir, "covers/$it.jpg") }
                when {
                    localFile != null && localFile.exists() -> localFile
                    item.audiobookCoverPath != null ->
                        // coverPath already contains the full API path (e.g. "/api/files/covers/audiobook_252.jpg")
                        coverImageUrl(serverUrl, item.audiobookCoverPath)
                    else -> null
                }
            }
            val variant = when (item.mediaType) {
                HomeItem.MediaType.PAIR -> BookCardVariant.Pair(
                    id = item.pairId ?: 0,
                    title = item.title,
                    author = item.author,
                    coverImageModel = coverModel,
                    series = item.series,
                    seriesIndex = item.seriesIndex,
                )
                HomeItem.MediaType.EBOOK -> BookCardVariant.SingleMedia(
                    id = item.ebookId ?: 0,
                    kind = BookCardVariant.SingleMedia.MediaKind.EBOOK,
                    title = item.title,
                    author = item.author,
                    series = item.series,
                    seriesIndex = item.seriesIndex,
                )
                HomeItem.MediaType.AUDIOBOOK -> BookCardVariant.SingleMedia(
                    id = item.audiobookId ?: 0,
                    kind = BookCardVariant.SingleMedia.MediaKind.AUDIOBOOK,
                    title = item.title,
                    author = item.author,
                    coverImageModel = coverModel,
                    series = item.series,
                    seriesIndex = item.seriesIndex,
                )
            }
            Box(modifier = Modifier.width(140.dp)) {
                BookCard(
                    variant = variant,
                    onClick = { onItemClick(item) },
                    onOverflow = { onItemOverflow(item) },
                    progress = (item.progressPercent / 100f).coerceIn(0f, 1f),
                )
            }
        }
    }
}

@Composable
private fun PairRow(
    pairs: List<BookPairEntity>,
    serverUrl: String,
    showNewBadge: Boolean,
    onPairClick: (BookPairEntity) -> Unit,
    onPairOverflow: (BookPairEntity) -> Unit,
) {
    val context = LocalContext.current
    LazyRow(
        contentPadding = PaddingValues(horizontal = 16.dp),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        items(pairs, key = { it.id }) { pair ->
            val coverModel = remember(pair.audiobookId, pair.audiobookCoverPath) {
                val localFile = File(context.filesDir, "covers/${pair.audiobookId}.jpg")
                when {
                    localFile.exists() -> localFile
                    pair.audiobookCoverPath != null ->
                        coverImageUrl(serverUrl, pair.audiobookCoverPath)
                    else -> null
                }
            }
            Box(modifier = Modifier.width(140.dp)) {
                BookCard(
                    variant = BookCardVariant.Pair(
                        id = pair.id,
                        title = pair.ebookTitle,
                        author = pair.ebookAuthor ?: pair.audiobookAuthor,
                        coverImageModel = coverModel,
                        hasEbookDownloaded = pair.ebookDownloaded,
                        hasAudiobookDownloaded = pair.audiobookDownloaded,
                        // Plan: mismatch warning appears only in the "New Pairs" section.
                        hasMismatchWarning = showNewBadge && pair.hasMetadataMismatch(),
                        series = pair.ebookSeries,
                        seriesIndex = pair.ebookSeriesIndex,
                    ),
                    onClick = { onPairClick(pair) },
                    onOverflow = { onPairOverflow(pair) },
                    status = if (showNewBadge) BadgeStatus.New else null,
                )
            }
        }
    }
}

@Composable
private fun QueueRow(
    items: List<HomeQueueItem>,
    onItemClick: (HomeQueueItem) -> Unit,
) {
    LazyRow(
        contentPadding = PaddingValues(horizontal = 16.dp),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        items(items, key = { it.pairId }) { queued ->
            val status = when (queued.status) {
                HomeQueueItem.QueueStatus.QUEUED       -> BadgeStatus.Queued
                HomeQueueItem.QueueStatus.TRANSCRIBING -> BadgeStatus.Transcribing(queued.percent)
            }
            Box(modifier = Modifier.width(140.dp)) {
                BookCard(
                    variant = BookCardVariant.Pair(
                        id = queued.pairId,
                        title = queued.title,
                        author = null,
                    ),
                    onClick = { onItemClick(queued) },
                    onOverflow = { /* overflow wired in Phase G */ },
                    status = status,
                )
            }
        }
    }
}

@Composable
private fun HomeEmptyState(webAppUrl: String, modifier: Modifier = Modifier) {
    val context = LocalContext.current
    Box(modifier = modifier, contentAlignment = Alignment.Center) {
        EmptyState(
            icon = Icons.AutoMirrored.Filled.LibraryBooks,
            title = "Your library is empty",
            subtitle = "Upload ebooks and audiobooks from the web app, then they'll show up here.",
            actionLabel = "Open web app",
            onAction = {
                val intent = Intent(Intent.ACTION_VIEW, webAppUrl.toUri())
                context.startActivity(intent)
            },
        )
    }
}

/**
 * Lightweight mismatch detection — flags cases where ebook and audiobook metadata disagree.
 * Used for the warning overlay on "New Pairs" cards.
 */
private fun BookPairEntity.hasMetadataMismatch(): Boolean {
    val titleDiffers = ebookTitle.trim().equals(audiobookTitle.trim(), ignoreCase = true).not()
    val authorDiffers = ebookAuthor?.trim().equals(audiobookAuthor?.trim(), ignoreCase = true).not() &&
        ebookAuthor != null && audiobookAuthor != null
    val mismatch = titleDiffers || authorDiffers
    if (mismatch) {
        android.util.Log.d(
            "MismatchDebug",
            "pair#$id titleDiffers=$titleDiffers authorDiffers=$authorDiffers " +
                "ebookTitle='$ebookTitle' audiobookTitle='$audiobookTitle' " +
                "ebookAuthor='$ebookAuthor' audiobookAuthor='$audiobookAuthor'"
        )
    }
    return mismatch
}

// ==========================================================================
// Overflow-target builders. Home carousels can carry pairs, standalone ebooks,
// or standalone audiobooks — each maps to one of CardOverflowMenu's three
// target shapes. The Pair builder folds in server-side transcription state
// (pair.status) plus any active queue items so the sheet can decide between
// Transcribe / Cancel transcription / Refresh sync data.
// ==========================================================================

private fun BookPairEntity.toPairOverflowTarget(activeTxPairIds: Set<Int>): OverflowTarget.Pair =
    OverflowTarget.Pair(
        pairId = id,
        title = ebookTitle,
        subtitle = ebookAuthor ?: audiobookAuthor,
        hasEbookDownloaded = ebookDownloaded,
        hasAudiobookDownloaded = audiobookDownloaded,
        isTranscribed = status == "synced",
        isQueuedOrTranscribing = id in activeTxPairIds || status == "transcribing",
        isComplete = false,
        hasMismatchWarning = hasMetadataMismatch(),
    )

private fun HomeItem.toOverflowTarget(
    recentlyAdded: List<BookPairEntity>,
    newPairs: List<BookPairEntity>,
    activeTxPairIds: Set<Int>,
): OverflowTarget = when (mediaType) {
    HomeItem.MediaType.PAIR -> {
        // Resolve the live pair from the flows we already collect so we can
        // use the same full-fidelity builder Recently Added / New Pairs use.
        val pair = (recentlyAdded + newPairs).firstOrNull { it.id == pairId }
        pair?.toPairOverflowTarget(activeTxPairIds)
            ?: OverflowTarget.Pair(
                pairId = pairId ?: 0,
                title = title,
                subtitle = author,
                hasEbookDownloaded = ebookDownloaded,
                hasAudiobookDownloaded = audiobookDownloaded,
                isTranscribed = false,
                isQueuedOrTranscribing = false,
                isComplete = false,
            )
    }
    HomeItem.MediaType.EBOOK -> OverflowTarget.Ebook(
        ebookId = ebookId ?: 0,
        title = title,
        subtitle = author,
        // We don't have the entity in hand for Continue Reading ebooks — default to
        // downloaded/paired=true so the destructive delete/pair options don't show.
        // Download shows only for not-downloaded items; since this item came from
        // getRecentlyReadEbooksFlow (which reads progress, not download state) we
        // conservatively assume the user has the file to read it.
        isDownloaded = true,
        isPaired = true,
    )
    HomeItem.MediaType.AUDIOBOOK -> OverflowTarget.Audiobook(
        audiobookId = audiobookId ?: 0,
        title = title,
        subtitle = author,
        isDownloaded = true,
        isPaired = true,
    )
}

/**
 * Build the [OverflowActions] for Home's sheet. Matches LibraryScreen's helper —
 * the VMs have parallel action methods so pair/ebook/audiobook behavior stays
 * consistent between the two screens.
 */
@Composable
private fun buildHomeOverflowActions(
    target: OverflowTarget,
    vm: HomeViewModel,
    onOpenPairReader: (Int) -> Unit,
    onOpenPairPlayer: (Int) -> Unit,
    onOpenEbook: (Int) -> Unit,
    onOpenAudiobook: (Int) -> Unit,
    onOpenPairDetails: (Int) -> Unit,
    onOpenEbookDetails: (Int) -> Unit,
    onOpenAudiobookDetails: (Int) -> Unit,
): OverflowActions {
    val isOnline by vm.isOnline.collectAsState()

    return when (target) {
        is OverflowTarget.Pair -> OverflowActions(
            isOnline      = isOnline,
            onViewDetails = { onOpenPairDetails(target.pairId) },
            onRead        = if (target.hasEbookDownloaded) { { onOpenPairReader(target.pairId) } } else null,
            onListen      = if (target.hasAudiobookDownloaded) { { onOpenPairPlayer(target.pairId) } } else null,
            onDownloadPair = if (!target.hasEbookDownloaded || !target.hasAudiobookDownloaded)
                { { vm.downloadBothById(target.pairId) } } else null,
        )
        is OverflowTarget.Ebook -> OverflowActions(
            isOnline      = isOnline,
            onViewDetails = { onOpenEbookDetails(target.ebookId) },
            onRead        = if (target.isDownloaded) { { onOpenEbook(target.ebookId) } } else null,
            onDownloadEbook = if (!target.isDownloaded) { { /* ebook entity not in scope on home */ } } else null,
        )
        is OverflowTarget.Audiobook -> OverflowActions(
            isOnline         = isOnline,
            onViewDetails    = { onOpenAudiobookDetails(target.audiobookId) },
            onListen         = if (target.isDownloaded) { { onOpenAudiobook(target.audiobookId) } } else null,
            onDownloadAudiobook = if (!target.isDownloaded) { { /* audiobook entity not in scope on home */ } } else null,
        )
        // Home has no series-grouped grid; series overflow is library-only.
        is OverflowTarget.Series -> OverflowActions(isOnline = isOnline)
    }
}

