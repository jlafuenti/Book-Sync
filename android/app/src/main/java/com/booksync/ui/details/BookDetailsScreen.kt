package com.booksync.ui.details

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Book
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.Headphones
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Sync
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CenterAlignedTopAppBar
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import coil.compose.AsyncImage
import coil.request.ImageRequest
import com.booksync.BuildConfig
import com.booksync.ui.components.ActionRow
import com.booksync.ui.theme.Tandem
import java.io.File

/**
 * Per-book landing page.
 *
 * Entry points:
 *   1. Overflow menu "View details" (every card)
 *   2. Tapping a pair that has neither ebook nor audiobook downloaded
 *
 * Handles 3 targets (pair / standalone ebook / standalone audiobook) — the VM
 * reads the id from its SavedStateHandle and picks the right entity flow.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun BookDetailsScreen(
    onBack: () -> Unit,
    onRead: (pairId: Int) -> Unit,
    onListen: (pairId: Int) -> Unit,
    onListenStandalone: (audiobookId: Int) -> Unit,
    viewModel: BookDetailsViewModel = hiltViewModel(),
) {
    val ui by viewModel.uiState.collectAsStateWithLifecycle()
    val colors = Tandem.colors
    val snackHost = remember { SnackbarHostState() }

    // Surface transient messages via the snackbar. Clear after display so a
    // second occurrence of the same text re-notifies.
    LaunchedEffect(ui.message) {
        ui.message?.let { msg ->
            snackHost.showSnackbar(msg)
            viewModel.clearMessage()
        }
    }

    // When the user unlinks a pair / deletes the standalone, the entity flow
    // emits null. Track "loaded at least once" so we only pop after a real
    // deletion, not on the initial null-before-first-emission.
    var hasLoaded by remember { mutableStateOf(false) }
    LaunchedEffect(ui.pair, ui.ebook, ui.audiobook) {
        val anyPresent = ui.pair != null || ui.ebook != null || ui.audiobook != null
        if (anyPresent) {
            hasLoaded = true
        } else if (hasLoaded) {
            onBack()
        }
    }

    // Confirm-dialog plumbing (shared across destructive + mild actions).
    var pending by remember { mutableStateOf<PendingConfirm?>(null) }

    Scaffold(
        topBar = {
            CenterAlignedTopAppBar(
                title = {
                    Text(
                        ui.title.ifBlank { "Book details" },
                        color = colors.textPrimary,
                        fontSize = 16.sp,
                        fontWeight = FontWeight.SemiBold,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(
                            Icons.AutoMirrored.Filled.ArrowBack,
                            contentDescription = "Back",
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
        snackbarHost = { SnackbarHost(snackHost) },
        containerColor = colors.bgPrimary,
    ) { padding ->
        if (ui.loading) {
            Box(Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.Center) {
                CircularProgressIndicator(color = colors.accent)
            }
            return@Scaffold
        }

        Column(
            modifier = Modifier
                .padding(padding)
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 16.dp, vertical = 16.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            Hero(ui = ui, serverUrl = viewModel.serverUrl)
            StatusChipRow(ui = ui)
            ui.description?.let { DescriptionBlock(description = it) }
            PrimaryActionButton(
                ui = ui,
                onRead = onRead,
                onListen = onListen,
                onListenStandalone = onListenStandalone,
                onDownloadPair = { viewModel.downloadPair() },
                onDownloadStandaloneEbook = { viewModel.downloadStandaloneEbook() },
                onDownloadStandaloneAudiobook = { viewModel.downloadStandaloneAudiobook() },
            )
            DownloadProgressFooter(ui = ui)

            HorizontalDivider(color = colors.border)

            // ----- Action stack -----
            Surface(
                shape = Tandem.shapes.card,
                color = colors.bgCard,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Column {
                    val pair = ui.pair
                    if (pair != null) {
                        if (!pair.ebookDownloaded) {
                            ActionRow(
                                title = "Download ebook only",
                                description = "Skip the audiobook for now.",
                                trailingIcon = Icons.Default.Download,
                                onClick = { viewModel.downloadEbookOnly() },
                            )
                        }
                        if (!pair.audiobookDownloaded) {
                            ActionRow(
                                title = "Download audiobook only",
                                description = "Skip the ebook for now.",
                                trailingIcon = Icons.Default.Download,
                                onClick = { viewModel.downloadAudiobookOnly() },
                            )
                        }
                        ActionRow(
                            title = "Refresh sync data",
                            description = "Re-download the latest sync map.",
                            trailingIcon = Icons.Default.Sync,
                            onClick = { viewModel.refreshSyncData() },
                        )
                        if (pair.ebookDownloaded) {
                            ActionRow(
                                title = "Delete ebook",
                                destructive = true,
                                onClick = {
                                    pending = PendingConfirm(
                                        title = "Delete ebook?",
                                        body = "This removes the local file. Your progress stays.",
                                        action = { viewModel.deleteEbook() },
                                    )
                                },
                            )
                        }
                        if (pair.audiobookDownloaded) {
                            ActionRow(
                                title = "Delete audiobook",
                                destructive = true,
                                onClick = {
                                    pending = PendingConfirm(
                                        title = "Delete audiobook?",
                                        body = "This removes the local file. Your progress stays.",
                                        action = { viewModel.deleteAudiobook() },
                                    )
                                },
                            )
                        }
                    }

                    val ebook = ui.ebook
                    if (ebook != null && ebook.isDownloaded) {
                        ActionRow(
                            title = "Delete ebook",
                            destructive = true,
                            onClick = {
                                pending = PendingConfirm(
                                    title = "Delete ebook?",
                                    body = "This removes the local file. Your progress stays.",
                                    action = { viewModel.deleteStandaloneEbook() },
                                )
                            },
                        )
                    }
                    val audio = ui.audiobook
                    if (audio != null && audio.isDownloaded) {
                        ActionRow(
                            title = "Delete audiobook",
                            destructive = true,
                            onClick = {
                                pending = PendingConfirm(
                                    title = "Delete audiobook?",
                                    body = "This removes the local file. Your progress stays.",
                                    action = { viewModel.deleteStandaloneAudiobook() },
                                )
                            },
                        )
                    }

                    ActionRow(
                        title = "Reset progress",
                        onClick = {
                            pending = PendingConfirm(
                                title = "Reset progress?",
                                body = "This clears your bookmark and position.",
                                action = { viewModel.resetProgress() },
                            )
                        },
                    )
                    ActionRow(
                        title = "Mark complete",
                        trailingIcon = Icons.Default.Check,
                        onClick = { viewModel.markComplete() },
                    )
                    if (pair != null) {
                        ActionRow(
                            title = "Unlink pair",
                            description = "Separate the ebook and audiobook. The files stay on the server.",
                            destructive = true,
                            onClick = {
                                pending = PendingConfirm(
                                    title = "Unlink pair?",
                                    body = "The ebook and audiobook will be shown separately. You can re-link them from the web app.",
                                    action = { viewModel.unlinkPair() },
                                )
                            },
                        )
                    }
                }
            }

            Spacer(Modifier.height(24.dp))
        }
    }

    pending?.let { p ->
        AlertDialog(
            onDismissRequest = { pending = null },
            title = { Text(p.title, color = colors.textPrimary) },
            text = { Text(p.body, color = colors.textSecondary) },
            confirmButton = {
                TextButton(onClick = { p.action(); pending = null }) {
                    Text("Confirm", color = colors.statusError, fontWeight = FontWeight.SemiBold)
                }
            },
            dismissButton = {
                TextButton(onClick = { pending = null }) {
                    Text("Cancel", color = colors.textSecondary)
                }
            },
            containerColor = colors.bgCard,
        )
    }
}

// ============================================================================
// Section composables
// ============================================================================

@Composable
private fun Hero(ui: BookDetailsUi, serverUrl: String) {
    val colors = Tandem.colors
    val context = LocalContext.current
    val coverModel = remember(ui.audiobookIdForCover, ui.audiobookCoverPath, serverUrl) {
        val local = ui.audiobookIdForCover?.let { File(context.filesDir, "covers/$it.jpg") }
        when {
            local != null && local.exists() -> local
            ui.audiobookCoverPath != null   -> "${serverUrl.trimEnd('/')}${ui.audiobookCoverPath}"
            else                            -> null
        }
    }
    val fallbackIcon = if (ui.audiobook != null && ui.pair == null)
        Icons.Default.Headphones else Icons.Default.Book

    Column(
        modifier = Modifier.fillMaxWidth(),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Box(
            modifier = Modifier
                .width(140.dp)
                .height(210.dp)
                .clip(RoundedCornerShape(10.dp))
                .background(colors.bgInput),
            contentAlignment = Alignment.Center,
        ) {
            // Gradient placeholder
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .background(
                        Brush.linearGradient(
                            listOf(
                                colors.accent.copy(alpha = 0.35f),
                                colors.accentSecondary.copy(alpha = 0.25f),
                            ),
                        ),
                    ),
                contentAlignment = Alignment.Center,
            ) {
                Icon(
                    imageVector = fallbackIcon,
                    contentDescription = null,
                    tint = Color.White.copy(alpha = 0.85f),
                    modifier = Modifier.size(40.dp),
                )
            }
            if (coverModel != null) {
                AsyncImage(
                    model = ImageRequest.Builder(context)
                        .data(coverModel)
                        .crossfade(true)
                        .build(),
                    contentDescription = null,
                    contentScale = ContentScale.Crop,
                    modifier = Modifier.fillMaxSize(),
                )
            }
        }
        Spacer(Modifier.height(12.dp))
        Text(
            ui.title,
            color = colors.textPrimary,
            fontSize = 20.sp,
            fontWeight = FontWeight.Bold,
        )
        ui.author?.let { author ->
            Spacer(Modifier.height(4.dp))
            Text(author, color = colors.textSecondary, fontSize = 14.sp)
        }
        ui.seriesLabel?.let { s ->
            Spacer(Modifier.height(4.dp))
            Text(s, color = colors.textMuted, fontSize = 12.sp)
        }
    }
}

/**
 * Collapsible description block. Shows ~6 lines by default with a "More" /
 * "Less" toggle when the text is longer. Matches the web app layout in
 * [BookDetailPage.jsx].
 */
@Composable
private fun DescriptionBlock(description: String) {
    val colors = Tandem.colors
    var expanded by remember { mutableStateOf(false) }
    Column(modifier = Modifier.fillMaxWidth()) {
        Text(
            "Description",
            color = colors.textMuted,
            fontSize = 12.sp,
            fontWeight = FontWeight.SemiBold,
        )
        Spacer(Modifier.height(6.dp))
        Text(
            text = description,
            color = colors.textPrimary,
            fontSize = 14.sp,
            maxLines = if (expanded) Int.MAX_VALUE else 6,
            overflow = TextOverflow.Ellipsis,
        )
        // Show toggle whenever the text is likely to overflow — cheap proxy
        // using length since TextLayoutResult round-trips through a callback.
        if (description.length > 260) {
            Spacer(Modifier.height(4.dp))
            TextButton(
                onClick = { expanded = !expanded },
                contentPadding = androidx.compose.foundation.layout.PaddingValues(0.dp),
            ) {
                Text(
                    if (expanded) "Show less" else "Show more",
                    color = colors.accent,
                    fontSize = 13.sp,
                    fontWeight = FontWeight.SemiBold,
                )
            }
        }
    }
}

@Composable
private fun StatusChipRow(ui: BookDetailsUi) {
    val chips = buildList {
        val pair = ui.pair
        if (pair != null) {
            add(StatusChip("Ebook", pair.ebookDownloaded))
            add(StatusChip("Audiobook", pair.audiobookDownloaded))
            add(StatusChip("Sync map", pair.syncMapDownloaded))
        }
        ui.ebook?.let  { add(StatusChip("Ebook",     it.isDownloaded)) }
        ui.audiobook?.let { add(StatusChip("Audiobook", it.isDownloaded)) }
    }
    if (chips.isEmpty()) return
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        chips.forEach { chip -> Chip(chip) }
    }
}

private data class StatusChip(val label: String, val present: Boolean)

@Composable
private fun Chip(chip: StatusChip) {
    val colors = Tandem.colors
    val bg = if (chip.present) colors.statusSuccess.copy(alpha = 0.18f) else colors.bgInput
    val fg = if (chip.present) colors.statusSuccess else colors.textMuted
    Surface(shape = Tandem.shapes.pill, color = bg) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp),
        ) {
            Icon(
                if (chip.present) Icons.Default.Check else Icons.Default.Close,
                contentDescription = null,
                tint = fg,
                modifier = Modifier.size(14.dp),
            )
            Spacer(Modifier.width(6.dp))
            Text(chip.label, color = fg, fontSize = 12.sp, fontWeight = FontWeight.Medium)
        }
    }
}

@Composable
private fun PrimaryActionButton(
    ui: BookDetailsUi,
    onRead: (Int) -> Unit,
    onListen: (Int) -> Unit,
    onListenStandalone: (Int) -> Unit,
    onDownloadPair: () -> Unit,
    onDownloadStandaloneEbook: () -> Unit,
    onDownloadStandaloneAudiobook: () -> Unit,
) {
    val colors = Tandem.colors
    val downloading = ui.downloadPercent != null

    data class ButtonSpec(
        val label: String,
        val icon: ImageVector,
        val onClick: () -> Unit,
    )

    val spec: ButtonSpec? = when {
        downloading -> null
        ui.pair != null && ui.pair.ebookDownloaded ->
            ButtonSpec("Read", Icons.Default.Book) { onRead(ui.pair.id) }
        ui.pair != null && ui.pair.audiobookDownloaded ->
            ButtonSpec("Listen", Icons.Default.PlayArrow) { onListen(ui.pair.id) }
        ui.pair != null ->
            ButtonSpec("Download pair", Icons.Default.Download, onDownloadPair)
        ui.ebook != null && !ui.ebook.isDownloaded ->
            ButtonSpec("Download ebook", Icons.Default.Download, onDownloadStandaloneEbook)
        ui.ebook != null && ui.ebook.isDownloaded ->
            // No standalone reader yet — the button disappears; user can still delete / reset from the stack.
            null
        ui.audiobook != null && !ui.audiobook.isDownloaded ->
            ButtonSpec("Download audiobook", Icons.Default.Download, onDownloadStandaloneAudiobook)
        ui.audiobook != null && ui.audiobook.isDownloaded ->
            ButtonSpec("Listen", Icons.Default.PlayArrow) { onListenStandalone(ui.audiobook.id) }
        else -> null
    }

    if (spec == null) return
    Button(
        onClick = spec.onClick,
        modifier = Modifier.fillMaxWidth(),
        colors = ButtonDefaults.buttonColors(containerColor = colors.accent),
    ) {
        Icon(spec.icon, contentDescription = null, modifier = Modifier.size(18.dp))
        Spacer(Modifier.width(8.dp))
        Text(spec.label, fontWeight = FontWeight.SemiBold)
    }
}

@Composable
private fun DownloadProgressFooter(ui: BookDetailsUi) {
    val pct = ui.downloadPercent ?: return
    val colors = Tandem.colors
    Column(modifier = Modifier.fillMaxWidth()) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                "Downloading… $pct%",
                color = colors.textSecondary,
                fontSize = 12.sp,
            )
        }
        Spacer(Modifier.height(6.dp))
        LinearProgressIndicator(
            progress = { (pct.coerceIn(0, 100) / 100f) },
            modifier = Modifier.fillMaxWidth(),
            color = colors.accent,
            trackColor = colors.bgInput,
        )
    }
}

// ============================================================================
// Confirm dialog plumbing
// ============================================================================

private data class PendingConfirm(
    val title: String,
    val body: String,
    val action: () -> Unit,
)
