package com.booksync.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AutoStories
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.CloudDownload
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.GraphicEq
import androidx.compose.material.icons.filled.Headphones
import androidx.compose.material.icons.filled.Link
import androidx.compose.material.icons.filled.LinkOff
import androidx.compose.material.icons.filled.Replay
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.filled.Sync
import androidx.compose.material.icons.filled.WarningAmber
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.booksync.ui.theme.Tandem

/**
 * Target of an overflow menu — drives which actions are shown.
 *
 * Pair targets offer per-media download/delete and Unlink.
 * Ebook/Audiobook targets offer "Pair with …" when still unpaired.
 *
 * All booleans (`hasEbookDownloaded`, `isTranscribed`, etc.) let the caller hide
 * or swap actions without threading a dozen args through the sheet.
 */
sealed class OverflowTarget {
    abstract val title: String
    abstract val subtitle: String?

    data class Pair(
        val pairId: Int,
        override val title: String,
        override val subtitle: String?,
        val hasEbookDownloaded: Boolean,
        val hasAudiobookDownloaded: Boolean,
        val isTranscribed: Boolean,
        val isQueuedOrTranscribing: Boolean,
        val isComplete: Boolean,
        val hasMismatchWarning: Boolean = false,
        val mismatchDetails: List<String> = emptyList(),
    ) : OverflowTarget()

    data class Ebook(
        val ebookId: Int,
        override val title: String,
        override val subtitle: String?,
        val isDownloaded: Boolean,
        val isPaired: Boolean,
    ) : OverflowTarget()

    data class Audiobook(
        val audiobookId: Int,
        override val title: String,
        override val subtitle: String?,
        val isDownloaded: Boolean,
        val isPaired: Boolean,
    ) : OverflowTarget()
}

/**
 * Hooks invoked when an action is tapped. All are optional so the caller only
 * needs to wire what's relevant. Actions the caller leaves null are simply not
 * offered.
 *
 * The [onTranscribeDisabled] message is surfaced as a disabled-button tooltip /
 * snackbar when offline or already queued.
 */
data class OverflowActions(
    val isOnline: Boolean = true,
    // Reading / listening shortcuts
    val onRead: (() -> Unit)? = null,
    val onListen: (() -> Unit)? = null,
    // Downloads
    val onDownloadEbook: (() -> Unit)? = null,
    val onDownloadAudiobook: (() -> Unit)? = null,
    val onDeleteEbook: (() -> Unit)? = null,
    val onDeleteAudiobook: (() -> Unit)? = null,
    // Transcription
    val onTranscribe: (() -> Unit)? = null,
    val onCancelTranscription: (() -> Unit)? = null,
    val onRefreshSyncData: (() -> Unit)? = null,
    // Progress
    val onMarkComplete: (() -> Unit)? = null,
    val onResetProgress: (() -> Unit)? = null,
    // Pairing
    val onPairWith: (() -> Unit)? = null,
    val onUnlinkPair: (() -> Unit)? = null,
)

/**
 * Card overflow bottom sheet.
 *
 * Context-aware content: Pair vs Ebook vs Audiobook changes which actions render.
 * Confirmation dialogs (unlink, delete, reset) are hosted in-component.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CardOverflowMenu(
    target: OverflowTarget,
    actions: OverflowActions,
    onDismiss: () -> Unit,
) {
    val colors = Tandem.colors
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)

    // Confirmation dialogs
    var confirmUnlink by remember { mutableStateOf(false) }
    var confirmDeleteEbook by remember { mutableStateOf(false) }
    var confirmDeleteAudiobook by remember { mutableStateOf(false) }
    var confirmReset by remember { mutableStateOf(false) }
    var confirmCancelTranscription by remember { mutableStateOf(false) }
    var showMismatch by remember { mutableStateOf(false) }

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
        containerColor = colors.bgSecondary,
        shape = Tandem.shapes.modal,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(start = 16.dp, end = 16.dp, bottom = 16.dp),
        ) {
            // Header
            Text(
                text = target.title,
                color = colors.textPrimary,
                fontSize = 16.sp,
                fontWeight = FontWeight.SemiBold,
                maxLines = 2,
            )
            target.subtitle?.let {
                Spacer(Modifier.height(2.dp))
                Text(text = it, color = colors.textSecondary, fontSize = 13.sp, maxLines = 1)
            }
            // Mismatch warning entry (Pair)
            if (target is OverflowTarget.Pair && target.hasMismatchWarning) {
                Spacer(Modifier.height(10.dp))
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(Tandem.shapes.button)
                        .background(colors.statusWarning.copy(alpha = 0.15f))
                        .clickable { showMismatch = true }
                        .padding(horizontal = 12.dp, vertical = 10.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Icon(
                        imageVector = Icons.Default.WarningAmber,
                        contentDescription = null,
                        tint = colors.statusWarning,
                        modifier = Modifier.size(18.dp),
                    )
                    Spacer(Modifier.width(10.dp))
                    Text(
                        text = "Pair has mismatches — tap for details",
                        color = colors.statusWarning,
                        fontSize = 13.sp,
                    )
                }
            }
            Spacer(Modifier.height(12.dp))

            // Action list
            when (target) {
                is OverflowTarget.Pair      -> PairActions(target, actions,
                    onConfirmUnlink    = { confirmUnlink = true },
                    onConfirmDeleteEb  = { confirmDeleteEbook = true },
                    onConfirmDeleteAud = { confirmDeleteAudiobook = true },
                    onConfirmReset     = { confirmReset = true },
                    onConfirmCancelTx  = { confirmCancelTranscription = true },
                )
                is OverflowTarget.Ebook     -> EbookActions(target, actions,
                    onConfirmDelete = { confirmDeleteEbook = true },
                    onConfirmReset  = { confirmReset = true },
                )
                is OverflowTarget.Audiobook -> AudiobookActions(target, actions,
                    onConfirmDelete = { confirmDeleteAudiobook = true },
                    onConfirmReset  = { confirmReset = true },
                )
            }
        }
    }

    // ----- Confirmations -----
    if (confirmUnlink && target is OverflowTarget.Pair) {
        ConfirmDialog(
            title = "Unlink pair?",
            message = "Unlinking will separate the ebook and audiobook. Your progress is kept on each.",
            confirmLabel = "Unlink",
            destructive = true,
            onConfirm = {
                confirmUnlink = false
                actions.onUnlinkPair?.invoke()
                onDismiss()
            },
            onCancel = { confirmUnlink = false },
        )
    }
    if (confirmDeleteEbook) {
        ConfirmDialog(
            title = "Delete ebook file?",
            message = "The ebook will be removed from this device. It will still be available on the server.",
            confirmLabel = "Delete",
            destructive = true,
            onConfirm = {
                confirmDeleteEbook = false
                actions.onDeleteEbook?.invoke()
                onDismiss()
            },
            onCancel = { confirmDeleteEbook = false },
        )
    }
    if (confirmDeleteAudiobook) {
        ConfirmDialog(
            title = "Delete audiobook file?",
            message = "The audiobook will be removed from this device. It will still be available on the server.",
            confirmLabel = "Delete",
            destructive = true,
            onConfirm = {
                confirmDeleteAudiobook = false
                actions.onDeleteAudiobook?.invoke()
                onDismiss()
            },
            onCancel = { confirmDeleteAudiobook = false },
        )
    }
    if (confirmReset) {
        ConfirmDialog(
            title = "Reset progress?",
            message = "Your current reading and listening position for this title will be cleared.",
            confirmLabel = "Reset",
            destructive = true,
            onConfirm = {
                confirmReset = false
                actions.onResetProgress?.invoke()
                onDismiss()
            },
            onCancel = { confirmReset = false },
        )
    }
    if (confirmCancelTranscription) {
        ConfirmDialog(
            title = "Cancel transcription?",
            message = "This pair will be removed from the transcription queue.",
            confirmLabel = "Cancel transcription",
            destructive = true,
            onConfirm = {
                confirmCancelTranscription = false
                actions.onCancelTranscription?.invoke()
                onDismiss()
            },
            onCancel = { confirmCancelTranscription = false },
        )
    }
    if (showMismatch && target is OverflowTarget.Pair) {
        AlertDialog(
            onDismissRequest = { showMismatch = false },
            confirmButton = {
                TextButton(onClick = { showMismatch = false }) { Text("OK") }
            },
            title = { Text("Pair mismatches") },
            text = {
                Column {
                    if (target.mismatchDetails.isEmpty()) {
                        Text("Ebook and audiobook metadata differ. Double-check this pair.")
                    } else {
                        target.mismatchDetails.forEach { line ->
                            Text("• $line", fontSize = 13.sp)
                        }
                    }
                }
            },
            containerColor = colors.bgSecondary,
        )
    }
}

// ---------- action lists per target type ----------

@Composable
private fun PairActions(
    target: OverflowTarget.Pair,
    actions: OverflowActions,
    onConfirmUnlink: () -> Unit,
    onConfirmDeleteEb: () -> Unit,
    onConfirmDeleteAud: () -> Unit,
    onConfirmReset: () -> Unit,
    onConfirmCancelTx: () -> Unit,
) {
    // Open shortcuts — only when action wired AND media present
    actions.onRead?.takeIf { target.hasEbookDownloaded }?.let {
        ActionRow(Icons.Default.AutoStories, "Read", onClick = it)
    }
    actions.onListen?.takeIf { target.hasAudiobookDownloaded }?.let {
        ActionRow(Icons.Default.Headphones, "Listen", onClick = it)
    }
    // Downloads
    if (!target.hasEbookDownloaded) {
        actions.onDownloadEbook?.let {
            ActionRow(Icons.Default.CloudDownload, "Download ebook", onClick = it)
        }
    } else {
        ActionRow(Icons.Default.Delete, "Delete ebook", destructive = true, onClick = onConfirmDeleteEb)
    }
    if (!target.hasAudiobookDownloaded) {
        actions.onDownloadAudiobook?.let {
            ActionRow(Icons.Default.CloudDownload, "Download audiobook", onClick = it)
        }
    } else {
        ActionRow(Icons.Default.Delete, "Delete audiobook", destructive = true, onClick = onConfirmDeleteAud)
    }
    // Transcription
    when {
        target.isTranscribed -> {
            actions.onRefreshSyncData?.let {
                ActionRow(Icons.Default.Sync, "Refresh sync data", onClick = it)
            }
        }
        target.isQueuedOrTranscribing -> {
            actions.onCancelTranscription?.let {
                ActionRow(Icons.Default.Stop, "Cancel transcription", destructive = true, onClick = onConfirmCancelTx)
            }
        }
        else -> {
            actions.onTranscribe?.let {
                val disabled = !actions.isOnline
                ActionRow(
                    icon = Icons.Default.GraphicEq,
                    label = if (disabled) "Transcribe (offline)" else "Transcribe",
                    enabled = !disabled,
                    onClick = it,
                )
            }
        }
    }
    // Progress
    if (!target.isComplete) {
        actions.onMarkComplete?.let {
            ActionRow(Icons.Default.CheckCircle, "Mark complete", onClick = it)
        }
    }
    actions.onResetProgress?.let {
        ActionRow(Icons.Default.Replay, "Reset progress", onClick = onConfirmReset)
    }
    // Pairing
    actions.onUnlinkPair?.let {
        ActionRow(Icons.Default.LinkOff, "Unlink pair", destructive = true, onClick = onConfirmUnlink)
    }
}

@Composable
private fun EbookActions(
    target: OverflowTarget.Ebook,
    actions: OverflowActions,
    onConfirmDelete: () -> Unit,
    onConfirmReset: () -> Unit,
) {
    actions.onRead?.takeIf { target.isDownloaded }?.let {
        ActionRow(Icons.Default.AutoStories, "Read", onClick = it)
    }
    if (!target.isDownloaded) {
        actions.onDownloadEbook?.let {
            ActionRow(Icons.Default.CloudDownload, "Download ebook", onClick = it)
        }
    } else {
        ActionRow(Icons.Default.Delete, "Delete ebook", destructive = true, onClick = onConfirmDelete)
    }
    actions.onMarkComplete?.let {
        ActionRow(Icons.Default.CheckCircle, "Mark complete", onClick = it)
    }
    actions.onResetProgress?.let {
        ActionRow(Icons.Default.Replay, "Reset progress", onClick = onConfirmReset)
    }
    if (!target.isPaired) {
        actions.onPairWith?.let {
            ActionRow(Icons.Default.Link, "Pair with audiobook…", onClick = it)
        }
    }
}

@Composable
private fun AudiobookActions(
    target: OverflowTarget.Audiobook,
    actions: OverflowActions,
    onConfirmDelete: () -> Unit,
    onConfirmReset: () -> Unit,
) {
    actions.onListen?.takeIf { target.isDownloaded }?.let {
        ActionRow(Icons.Default.Headphones, "Listen", onClick = it)
    }
    if (!target.isDownloaded) {
        actions.onDownloadAudiobook?.let {
            ActionRow(Icons.Default.CloudDownload, "Download audiobook", onClick = it)
        }
    } else {
        ActionRow(Icons.Default.Delete, "Delete audiobook", destructive = true, onClick = onConfirmDelete)
    }
    actions.onMarkComplete?.let {
        ActionRow(Icons.Default.CheckCircle, "Mark complete", onClick = it)
    }
    actions.onResetProgress?.let {
        ActionRow(Icons.Default.Replay, "Reset progress", onClick = onConfirmReset)
    }
    if (!target.isPaired) {
        actions.onPairWith?.let {
            ActionRow(Icons.Default.Link, "Pair with ebook…", onClick = it)
        }
    }
}

// ---------- helpers ----------

@Composable
private fun ActionRow(
    icon: ImageVector,
    label: String,
    onClick: () -> Unit,
    destructive: Boolean = false,
    enabled: Boolean = true,
) {
    val colors = Tandem.colors
    val textColor = when {
        !enabled    -> colors.textMuted
        destructive -> colors.statusError
        else        -> colors.textPrimary
    }
    val iconBg = when {
        !enabled    -> colors.textMuted.copy(alpha = 0.12f)
        destructive -> colors.statusError.copy(alpha = 0.15f)
        else        -> colors.accentLight
    }
    val iconTint = when {
        !enabled    -> colors.textMuted
        destructive -> colors.statusError
        else        -> colors.accent
    }
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(Tandem.shapes.button)
            .clickable(enabled = enabled, onClick = onClick)
            .padding(horizontal = 8.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.Start,
    ) {
        Box(
            modifier = Modifier
                .size(32.dp)
                .clip(CircleShape)
                .background(iconBg),
            contentAlignment = Alignment.Center,
        ) {
            Icon(
                imageVector = icon,
                contentDescription = null,
                tint = iconTint,
                modifier = Modifier.size(18.dp),
            )
        }
        Spacer(Modifier.width(14.dp))
        Text(
            text = label,
            color = textColor,
            fontSize = 14.sp,
            fontWeight = FontWeight.Medium,
        )
    }
}

@Composable
private fun ConfirmDialog(
    title: String,
    message: String,
    confirmLabel: String,
    destructive: Boolean,
    onConfirm: () -> Unit,
    onCancel: () -> Unit,
) {
    val colors = Tandem.colors
    AlertDialog(
        onDismissRequest = onCancel,
        title = { Text(title) },
        text = { Text(message) },
        confirmButton = {
            TextButton(onClick = onConfirm) {
                Text(
                    confirmLabel,
                    color = if (destructive) colors.statusError else colors.accent,
                )
            }
        },
        dismissButton = {
            TextButton(onClick = onCancel) {
                Text("Keep", color = colors.textSecondary)
            }
        },
        containerColor = colors.bgSecondary,
        titleContentColor = colors.textPrimary,
        textContentColor = colors.textSecondary,
    )
}

