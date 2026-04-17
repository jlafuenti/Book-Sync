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
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.net.toUri
import androidx.hilt.navigation.compose.hiltViewModel
import com.booksync.data.local.entity.BookPairEntity
import com.booksync.ui.components.BadgeStatus
import com.booksync.ui.components.BookCard
import com.booksync.ui.components.BookCardVariant
import com.booksync.ui.components.EmptyState
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
    onSeeAll: (route: HomeSeeAll) -> Unit,
    viewModel: HomeViewModel = hiltViewModel(),
) {
    val colors = Tandem.colors
    val continueItems by viewModel.continueItems.collectAsState()
    val recentlyAdded by viewModel.recentlyAdded.collectAsState()
    val newPairs by viewModel.newPairs.collectAsState()
    val queueItems by viewModel.queueItems.collectAsState()

    val allEmpty = continueItems.isEmpty() &&
        recentlyAdded.isEmpty() &&
        newPairs.isEmpty() &&
        queueItems.isEmpty()

    Scaffold(
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
            HomeEmptyState(
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
                        onItemClick = { item ->
                            when (item.mediaType) {
                                HomeItem.MediaType.PAIR      -> item.pairId?.let(onOpenPairReader)
                                HomeItem.MediaType.EBOOK     -> item.ebookId?.let(onOpenEbook)
                                HomeItem.MediaType.AUDIOBOOK -> item.audiobookId?.let(onOpenAudiobook)
                            }
                        },
                        onItemLongClick = { /* overflow wired when cards gain long-press */ },
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
                        showNewBadge = false,
                        onPairClick = { pair -> onOpenPairReader(pair.id) },
                    )
                }
            }

            if (newPairs.isNotEmpty()) {
                item {
                    SectionHeader(
                        title = "New Pairs",
                        onSeeAll = { onSeeAll(HomeSeeAll.NEW) },
                    )
                    PairRow(
                        pairs = newPairs,
                        showNewBadge = true,
                        onPairClick = { pair -> onOpenPairReader(pair.id) },
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
                        onItemClick = { q -> onOpenPairReader(q.pairId) },
                    )
                }
            }
        }
    }
}

/** Deep-link hint passed to the caller so it can build the matching library URL. */
enum class HomeSeeAll { CONTINUE, RECENTLY_ADDED, NEW, QUEUE }

// --------------------------------------------------------------------------
// Subcomponents
// --------------------------------------------------------------------------

@Composable
private fun SectionHeader(title: String, onSeeAll: () -> Unit) {
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
    onItemClick: (HomeItem) -> Unit,
    onItemLongClick: (HomeItem) -> Unit,
) {
    LazyRow(
        contentPadding = PaddingValues(horizontal = 16.dp),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        items(items, key = { it.id }) { item ->
            val variant = when (item.mediaType) {
                HomeItem.MediaType.PAIR -> BookCardVariant.Pair(
                    id = item.pairId ?: 0,
                    title = item.title,
                    author = item.author,
                )
                HomeItem.MediaType.EBOOK -> BookCardVariant.SingleMedia(
                    id = item.ebookId ?: 0,
                    kind = BookCardVariant.SingleMedia.MediaKind.EBOOK,
                    title = item.title,
                    author = item.author,
                )
                HomeItem.MediaType.AUDIOBOOK -> BookCardVariant.SingleMedia(
                    id = item.audiobookId ?: 0,
                    kind = BookCardVariant.SingleMedia.MediaKind.AUDIOBOOK,
                    title = item.title,
                    author = item.author,
                )
            }
            Box(modifier = Modifier.width(140.dp)) {
                BookCard(
                    variant = variant,
                    onClick = { onItemClick(item) },
                    onOverflow = { onItemLongClick(item) },
                    progress = (item.progressPercent / 100f).coerceIn(0f, 1f),
                )
            }
        }
    }
}

@Composable
private fun PairRow(
    pairs: List<BookPairEntity>,
    showNewBadge: Boolean,
    onPairClick: (BookPairEntity) -> Unit,
) {
    LazyRow(
        contentPadding = PaddingValues(horizontal = 16.dp),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        items(pairs, key = { it.id }) { pair ->
            Box(modifier = Modifier.width(140.dp)) {
                BookCard(
                    variant = BookCardVariant.Pair(
                        id = pair.id,
                        title = pair.ebookTitle,
                        author = pair.ebookAuthor ?: pair.audiobookAuthor,
                        hasEbookDownloaded = pair.ebookDownloaded,
                        hasAudiobookDownloaded = pair.audiobookDownloaded,
                        hasMismatchWarning = pair.hasMetadataMismatch(),
                    ),
                    onClick = { onPairClick(pair) },
                    onOverflow = { /* overflow wired via LibraryScreen in Phase D */ },
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
private fun HomeEmptyState(modifier: Modifier = Modifier) {
    val context = LocalContext.current
    Box(modifier = modifier, contentAlignment = Alignment.Center) {
        EmptyState(
            icon = Icons.AutoMirrored.Filled.LibraryBooks,
            title = "Your library is empty",
            subtitle = "Upload ebooks and audiobooks from the web app, then they'll show up here.",
            actionLabel = "Open web app",
            onAction = {
                val intent = Intent(Intent.ACTION_VIEW, "https://booksync.lafuenti.com".toUri())
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
    return titleDiffers || authorDiffers
}

