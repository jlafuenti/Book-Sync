package com.booksync.ui.components

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.booksync.ui.theme.Tandem

/**
 * The visual states a badge can render. Mirrors the transcription / download lifecycle
 * plus the "NEW" marker used for unacknowledged items.
 *
 * Badges are designed to fit the card top-right corner — short labels, 10sp uppercase.
 */
sealed class BadgeStatus(val label: String) {
    /** Pair has a sync map downloaded and ready. */
    object Synced : BadgeStatus("SYNCED")

    /** Pair has been queued for transcription but hasn't started yet. */
    object Queued : BadgeStatus("QUEUED")

    /** Transcription in progress. */
    data class Transcribing(val percent: Int) : BadgeStatus("TRANSCRIBING ${percent}%")

    /** Pair is fully transcribed on the server. */
    object Transcribed : BadgeStatus("TRANSCRIBED")

    /** Pair has never been transcribed. */
    object NotTranscribed : BadgeStatus("NOT TRANSCRIBED")

    /** Last transcription attempt failed. */
    object Failed : BadgeStatus("FAILED")

    /** Item is unacknowledged (new to the user). */
    object New : BadgeStatus("NEW")

    /** Active download progress (used while DownloadWorker runs). */
    data class Downloading(val percent: Int) : BadgeStatus("DOWNLOADING ${percent}%")

    /** Local-only media not yet paired to its counterpart. */
    object Unpaired : BadgeStatus("UNPAIRED")
}

/**
 * StatusBadge — tiny uppercase 10sp chip.
 *
 * Visual: status color at ~20% alpha fill, full status color for text.
 * The Transcribing / Queued states animate a pulsing dot so in-progress work reads at a glance.
 */
@Composable
fun StatusBadge(
    status: BadgeStatus,
    modifier: Modifier = Modifier,
) {
    val colors = Tandem.colors
    val tone: Color = when (status) {
        is BadgeStatus.Synced,
        is BadgeStatus.Transcribed      -> colors.statusSuccess
        is BadgeStatus.Queued,
        is BadgeStatus.Transcribing,
        is BadgeStatus.Downloading      -> colors.statusInfo
        is BadgeStatus.NotTranscribed,
        is BadgeStatus.Unpaired         -> colors.textMuted
        is BadgeStatus.Failed           -> colors.statusError
        is BadgeStatus.New              -> colors.accent
    }

    val animated = status is BadgeStatus.Queued ||
        status is BadgeStatus.Transcribing ||
        status is BadgeStatus.Downloading

    Row(
        modifier = modifier
            .clip(RoundedCornerShape(6.dp))
            .background(tone.copy(alpha = 0.20f))
            .padding(horizontal = 6.dp, vertical = 3.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        if (animated) {
            PulsingDot(color = tone)
            Spacer(Modifier.width(2.dp))
        }
        Text(
            text = status.label,
            color = tone,
            fontSize = 10.sp,
            fontWeight = FontWeight.SemiBold,
            letterSpacing = 0.4.sp,
        )
    }
}

/** 6dp pulsing dot used for active states (Queued / Transcribing / Downloading). */
@Composable
private fun PulsingDot(color: Color) {
    val transition = rememberInfiniteTransition(label = "badge-pulse")
    val alpha by transition.animateFloat(
        initialValue = 0.35f,
        targetValue = 1.0f,
        animationSpec = infiniteRepeatable(
            animation = tween(durationMillis = 900),
            repeatMode = RepeatMode.Reverse,
        ),
        label = "badge-pulse-alpha",
    )
    Box(
        modifier = Modifier
            .size(6.dp)
            .alpha(alpha)
            .clip(CircleShape)
            .background(color),
    )
}
