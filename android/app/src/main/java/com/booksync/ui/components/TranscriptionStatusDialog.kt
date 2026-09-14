package com.booksync.ui.components

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.booksync.data.remote.dto.TranscriptionStatus
import com.booksync.ui.theme.Tandem

/**
 * "Not synced yet" — shown before opening or switching to a pair with no sync
 * map (issue #536). [status] is what [com.booksync.data.repository.PairReadiness.readinessFor]
 * returned; it is never [TranscriptionStatus.Transcribed], since that is
 * exactly the case readiness treats as ready (no dialog).
 *
 * [continueLabel] ("Open anyway" / "Switch anyway") is always offered, as the
 * dialog's confirm action. Editors and above ([canTranscribe]) additionally
 * get a Transcribe / Try again button wherever the state allows starting a
 * transcription; everyone else sees a line asking them to find someone who
 * can. Both actions that reach the server ([onTranscribe]) are disabled while
 * [isOnline] is false.
 */
@Composable
fun TranscriptionStatusDialog(
    status: TranscriptionStatus,
    canTranscribe: Boolean,
    isOnline: Boolean,
    continueLabel: String,
    onContinue: () -> Unit,
    onTranscribe: () -> Unit,
    onDismiss: () -> Unit,
) {
    val colors = Tandem.colors
    val askAnOperator = "Ask whoever runs your Tandem server to transcribe it."

    val bodyLines: List<String>
    val transcribeLabel: String?

    when (status) {
        is TranscriptionStatus.NotTranscribed -> {
            transcribeLabel = if (canTranscribe) "Transcribe" else null
            bodyLines = buildList {
                add(
                    "This book hasn't been transcribed, so your place won't carry " +
                        "over between the ebook and the audiobook."
                )
                if (!canTranscribe) add(askAnOperator)
            }
        }
        is TranscriptionStatus.Queued -> {
            transcribeLabel = null
            bodyLines = listOf(
                if (status.position > 0) {
                    "It's in the transcription queue (position ${status.position}). " +
                        "Your place will start carrying over once it's done — the sync data downloads on its own."
                } else {
                    "It's in the transcription queue. Your place will start carrying " +
                        "over once it's done — the sync data downloads on its own."
                }
            )
        }
        is TranscriptionStatus.Transcribing -> {
            transcribeLabel = null
            bodyLines = listOf(
                "Transcribing now, ${status.progressPercent}%. Your place will start " +
                    "carrying over once it's done — the sync data downloads on its own."
            )
        }
        is TranscriptionStatus.Failed -> {
            transcribeLabel = if (canTranscribe) "Try again" else null
            bodyLines = buildList {
                add("Transcription failed.")
                status.message?.takeIf { it.isNotBlank() }?.let { add(it) }
                if (!canTranscribe) add(askAnOperator)
            }
        }
        TranscriptionStatus.Transcribed -> {
            // Not expected — readiness() returns null (no dialog) once synced.
            transcribeLabel = null
            bodyLines = emptyList()
        }
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Not synced yet") },
        text = {
            Column {
                bodyLines.forEachIndexed { index, line ->
                    if (index > 0) Spacer(Modifier.height(8.dp))
                    Text(line)
                }
            }
        },
        confirmButton = {
            TextButton(onClick = onContinue) {
                Text(continueLabel, color = colors.accent)
            }
        },
        dismissButton = transcribeLabel?.let { label ->
            {
                TextButton(onClick = onTranscribe, enabled = isOnline) {
                    Text(label, color = colors.accent)
                }
            }
        },
        containerColor = colors.bgSecondary,
        titleContentColor = colors.textPrimary,
        textContentColor = colors.textSecondary,
    )
}
