package com.booksync.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Headphones
import androidx.compose.material.icons.filled.Book
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.WarningAmber
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.ProgressIndicatorDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import coil.compose.AsyncImage
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.booksync.ui.theme.Tandem

/**
 * BookCard — the single card component used everywhere a book is rendered.
 *
 * Variants:
 *   - [BookCardVariant.Pair]          full pair with ebook + audiobook (library + continue reading)
 *   - [BookCardVariant.SingleMedia]   standalone ebook or audiobook
 *   - [BookCardVariant.SeriesStack]   fanned-out stack of 3 mini-covers in series mode
 *
 * Slots:
 *   cover (2:3 aspect) → title → author, plus:
 *     - top-right [StatusBadge] for transcription / NEW / offline states
 *     - top-left mismatch warning overlay (pair only)
 *     - bottom progress bar (0..1) pinned to the cover
 *     - bottom-right three-dot overflow button
 *
 * Pass [downloadPercent] to swap the badge/progress for an active download; this takes
 * precedence over [status] and [progress] for the duration of the download.
 */
sealed class BookCardVariant {
    data class Pair(
        val id: Int,
        val title: String,
        val author: String?,
        val coverImageModel: Any? = null,      // File, Uri, URL string — loaded by Coil
        val hasEbookDownloaded: Boolean = false,
        val hasAudiobookDownloaded: Boolean = false,
        val hasMismatchWarning: Boolean = false,
    ) : BookCardVariant()

    data class SingleMedia(
        val id: Int,
        val kind: MediaKind,
        val title: String,
        val author: String?,
        val coverImageModel: Any? = null,      // File, Uri, URL string — loaded by Coil
        val isDownloaded: Boolean = false,
    ) : BookCardVariant() {
        enum class MediaKind { EBOOK, AUDIOBOOK }
    }

    data class SeriesStack(
        val seriesName: String,
        val author: String?,
        val itemCount: Int,
        val pairCount: Int,
        val ebookCount: Int,
        val audiobookCount: Int,
        val coverImageModels: List<Any?> = emptyList(), // up to 3, back-to-front
    ) : BookCardVariant()
}

@Composable
fun BookCard(
    variant: BookCardVariant,
    onClick: () -> Unit,
    onOverflow: () -> Unit,
    modifier: Modifier = Modifier,
    status: BadgeStatus? = null,
    progress: Float? = null,            // 0.0..1.0 reading/listening progress
    downloadPercent: Int? = null,       // overrides status+progress while downloading
) {
    val colors = Tandem.colors
    val shapes = Tandem.shapes

    Surface(
        modifier = modifier
            .clip(shapes.card)
            .clickable(onClick = onClick),
        shape = shapes.card,
        color = colors.bgCard,
    ) {
        Column(modifier = Modifier.padding(8.dp)) {
            // 2:3 cover area with overlays
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .aspectRatio(2f / 3f)
                    .clip(RoundedCornerShape(8.dp))
                    .background(colors.bgInput),
            ) {
                CoverContent(variant)

                // Top-left mismatch warning (Pair only)
                val showMismatch = (variant as? BookCardVariant.Pair)?.hasMismatchWarning == true
                if (showMismatch) {
                    Surface(
                        shape = RoundedCornerShape(6.dp),
                        color = colors.statusWarning.copy(alpha = 0.95f),
                        modifier = Modifier
                            .align(Alignment.TopStart)
                            .padding(6.dp),
                    ) {
                        Icon(
                            imageVector = Icons.Default.WarningAmber,
                            contentDescription = "Mismatch warning",
                            tint = Color.White,
                            modifier = Modifier.padding(4.dp).size(14.dp),
                        )
                    }
                }

                // Top-right status badge — download progress takes precedence
                val effectiveStatus = when {
                    downloadPercent != null -> BadgeStatus.Downloading(downloadPercent)
                    status != null          -> status
                    else                    -> null
                }
                if (effectiveStatus != null) {
                    StatusBadge(
                        status = effectiveStatus,
                        modifier = Modifier
                            .align(Alignment.TopEnd)
                            .padding(6.dp),
                    )
                }

                // Bottom progress — downloadPercent beats reading progress
                val barFraction: Float? = when {
                    downloadPercent != null -> (downloadPercent / 100f).coerceIn(0f, 1f)
                    progress != null        -> progress.coerceIn(0f, 1f)
                    else                    -> null
                }
                val barColor = if (downloadPercent != null) colors.statusInfo else colors.accent
                if (barFraction != null) {
                    LinearProgressIndicator(
                        progress = { barFraction },
                        modifier = Modifier
                            .align(Alignment.BottomCenter)
                            .fillMaxWidth()
                            .height(4.dp),
                        color = barColor,
                        trackColor = Color.Black.copy(alpha = 0.35f),
                        strokeCap = ProgressIndicatorDefaults.LinearStrokeCap,
                    )
                }
            }

            Spacer(Modifier.height(10.dp))

            // Title / author / overflow row
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.Top,
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = cardTitle(variant),
                        color = colors.textPrimary,
                        fontSize = 14.sp,
                        fontWeight = FontWeight.SemiBold,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                    )
                    cardSubtitle(variant)?.let { subtitle ->
                        Spacer(Modifier.height(2.dp))
                        Text(
                            text = subtitle,
                            color = colors.textSecondary,
                            fontSize = 12.sp,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                    // Series-stack count chips
                    if (variant is BookCardVariant.SeriesStack) {
                        Spacer(Modifier.height(6.dp))
                        SeriesCountRow(
                            pairs = variant.pairCount,
                            ebooks = variant.ebookCount,
                            audiobooks = variant.audiobookCount,
                        )
                    }
                    // Media type chip for single-media cards
                    if (variant is BookCardVariant.SingleMedia) {
                        Spacer(Modifier.height(6.dp))
                        MediaKindChip(kind = variant.kind)
                    }
                }

                IconButton(
                    onClick = onOverflow,
                    modifier = Modifier.size(32.dp),
                ) {
                    Icon(
                        imageVector = Icons.Default.MoreVert,
                        contentDescription = "More actions",
                        tint = colors.textSecondary,
                    )
                }
            }
        }
    }
}

// ---------- internals ----------

@Composable
private fun BoxScope.CoverContent(variant: BookCardVariant) {
    when (variant) {
        is BookCardVariant.Pair         -> CoverOrPlaceholder(variant.coverImageModel, Icons.Default.Book)
        is BookCardVariant.SingleMedia  -> {
            val fallback = if (variant.kind == BookCardVariant.SingleMedia.MediaKind.AUDIOBOOK)
                Icons.Default.Headphones else Icons.Default.Book
            CoverOrPlaceholder(variant.coverImageModel, fallback)
        }
        is BookCardVariant.SeriesStack  -> SeriesStackCovers(variant.coverImageModels)
    }
}

@Composable
private fun BoxScope.CoverOrPlaceholder(
    coverModel: Any?,
    fallbackIcon: androidx.compose.ui.graphics.vector.ImageVector,
) {
    val colors = Tandem.colors
    // Gradient placeholder — always drawn first; covered by the image once Coil loads it.
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
    // Coil image overlays the placeholder; if it fails to load, placeholder remains visible.
    if (coverModel != null) {
        AsyncImage(
            model = coverModel,
            contentDescription = null,
            contentScale = ContentScale.Crop,
            modifier = Modifier.fillMaxSize(),
        )
    }
}

@Composable
private fun BoxScope.SeriesStackCovers(covers: List<Any?>) {
    // Back-to-front, 3 tilted mini-covers fanned out (web SeriesCard look).
    val rotations = listOf(-8f, -2f, 6f)
    val offsets = listOf(-14f, 0f, 14f)
    val padded = (covers + List(3) { null }).take(3)

    padded.forEachIndexed { i, model ->
        Box(
            modifier = Modifier
                .align(Alignment.Center)
                .graphicsLayer {
                    rotationZ = rotations[i]
                    translationX = offsets[i] * density
                }
                .size(width = 86.dp, height = 128.dp)
                .clip(RoundedCornerShape(6.dp))
                .background(Tandem.colors.bgCardHover),
            contentAlignment = Alignment.Center,
        ) {
            Icon(
                imageVector = Icons.Default.Book,
                contentDescription = null,
                tint = Tandem.colors.textMuted,
                modifier = Modifier.size(28.dp),
            )
            if (model != null) {
                AsyncImage(
                    model = model,
                    contentDescription = null,
                    contentScale = ContentScale.Crop,
                    modifier = Modifier.fillMaxSize(),
                )
            }
        }
    }
}

@Composable
private fun SeriesCountRow(pairs: Int, ebooks: Int, audiobooks: Int) {
    val colors = Tandem.colors
    Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        if (pairs > 0)      CountChip(text = "$pairs pair", color = colors.accent)
        if (ebooks > 0)     CountChip(text = "$ebooks ebk", color = colors.statusInfo)
        if (audiobooks > 0) CountChip(text = "$audiobooks aud", color = colors.statusSuccess)
    }
}

@Composable
private fun CountChip(text: String, color: Color) {
    Surface(
        shape = RoundedCornerShape(4.dp),
        color = color.copy(alpha = 0.18f),
    ) {
        Text(
            text = text,
            color = color,
            fontSize = 10.sp,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.padding(horizontal = 5.dp, vertical = 1.dp),
        )
    }
}

@Composable
private fun MediaKindChip(kind: BookCardVariant.SingleMedia.MediaKind) {
    val colors = Tandem.colors
    val (label, tone) = when (kind) {
        BookCardVariant.SingleMedia.MediaKind.EBOOK     -> "EBOOK"     to colors.statusInfo
        BookCardVariant.SingleMedia.MediaKind.AUDIOBOOK -> "AUDIOBOOK" to colors.statusSuccess
    }
    Surface(
        shape = RoundedCornerShape(4.dp),
        color = tone.copy(alpha = 0.18f),
    ) {
        Text(
            text = label,
            color = tone,
            fontSize = 9.sp,
            fontWeight = FontWeight.SemiBold,
            letterSpacing = 0.4.sp,
            modifier = Modifier.padding(horizontal = 5.dp, vertical = 1.dp),
        )
    }
}

private fun cardTitle(variant: BookCardVariant): String = when (variant) {
    is BookCardVariant.Pair         -> variant.title
    is BookCardVariant.SingleMedia  -> variant.title
    is BookCardVariant.SeriesStack  -> variant.seriesName
}

private fun cardSubtitle(variant: BookCardVariant): String? = when (variant) {
    is BookCardVariant.Pair         -> variant.author
    is BookCardVariant.SingleMedia  -> variant.author
    is BookCardVariant.SeriesStack  -> variant.author
}
