package com.booksync.ui.components

import android.content.ComponentName
import android.graphics.BitmapFactory
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.Image
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Headphones
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.media3.common.MediaMetadata
import androidx.media3.common.Player
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import com.booksync.player.AudioPlayerService
import com.booksync.player.MediaId
import com.booksync.ui.theme.Tandem
import kotlinx.coroutines.delay

/**
 * Compact audio status bar above the bottom nav.
 *
 * Connects to [AudioPlayerService] via a local [MediaController], observes the current
 * media item, and renders cover + title + play/pause + a thin progress bar. Tapping the
 * body invokes [onExpand] with the current mediaId so the caller can route to the full
 * Player (e.g. `player/{pairId}` when the id starts with `pair_`).
 *
 * Returns without rendering when no media is loaded. Composes cheaply in that state so it
 * can live unconditionally in the app scaffold.
 */
@Composable
fun MiniPlayerBar(
    modifier: Modifier = Modifier,
    onExpand: (mediaId: String) -> Unit,
) {
    val context = LocalContext.current
    var controller by remember { mutableStateOf<MediaController?>(null) }

    // State mirrors from the MediaController
    var mediaId by remember { mutableStateOf<String?>(null) }
    var title by remember { mutableStateOf<String?>(null) }
    var artist by remember { mutableStateOf<String?>(null) }
    var isPlaying by remember { mutableStateOf(false) }
    var positionMs by remember { mutableStateOf(0L) }
    var durationMs by remember { mutableStateOf(0L) }
    var coverBitmap by remember { mutableStateOf<android.graphics.Bitmap?>(null) }

    // Establish the MediaController and register listeners for the lifetime of the composable
    DisposableEffect(context) {
        val token = SessionToken(
            context.applicationContext,
            ComponentName(context.applicationContext, AudioPlayerService::class.java),
        )
        val future = MediaController.Builder(context.applicationContext, token).buildAsync()
        val listener = object : Player.Listener {
            override fun onIsPlayingChanged(isPlayingNow: Boolean) {
                isPlaying = isPlayingNow
            }
            override fun onMediaMetadataChanged(metadata: MediaMetadata) {
                title = metadata.title?.toString()
                artist = metadata.artist?.toString() ?: metadata.albumArtist?.toString()
                metadata.artworkData?.let { data ->
                    runCatching { BitmapFactory.decodeByteArray(data, 0, data.size) }
                        .getOrNull()
                        ?.let { coverBitmap = it }
                }
            }
        }
        future.addListener({
            runCatching { future.get() }.getOrNull()?.let { c ->
                controller = c
                // Seed state
                mediaId = c.currentMediaItem?.mediaId
                title = c.currentMediaItem?.mediaMetadata?.title?.toString()
                artist = c.currentMediaItem?.mediaMetadata?.artist?.toString()
                    ?: c.currentMediaItem?.mediaMetadata?.albumArtist?.toString()
                isPlaying = c.isPlaying
                durationMs = c.duration.takeIf { it > 0 } ?: 0L
                c.currentMediaItem?.mediaMetadata?.artworkData?.let { data ->
                    runCatching { BitmapFactory.decodeByteArray(data, 0, data.size) }
                        .getOrNull()
                        ?.let { coverBitmap = it }
                }
                c.addListener(listener)
            }
        }, { it.run() })
        onDispose {
            runCatching { controller?.removeListener(listener) }
            runCatching { controller?.release() }
            controller = null
        }
    }

    // Poll position + mediaId changes (covers the case where the id changes without
    // a metadata event, and keeps the progress bar moving).
    LaunchedEffect(controller) {
        while (controller != null) {
            val c = controller ?: break
            mediaId = c.currentMediaItem?.mediaId
            positionMs = c.currentPosition.coerceAtLeast(0L)
            durationMs = c.duration.takeIf { it > 0 } ?: 0L
            delay(500)
        }
    }

    val effectiveMediaId = mediaId
    if (effectiveMediaId.isNullOrBlank()) return  // no media → no bar

    val colors = Tandem.colors
    val progress = if (durationMs > 0) (positionMs.toFloat() / durationMs) else 0f

    Column(
        modifier = modifier
            .fillMaxWidth()
            .background(colors.bgSecondary.copy(alpha = 0.96f))
            .clickable { onExpand(effectiveMediaId) },
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            // Cover
            Box(
                modifier = Modifier
                    .size(44.dp)
                    .clip(Tandem.shapes.button)
                    .background(colors.bgInput),
                contentAlignment = Alignment.Center,
            ) {
                val bmp = coverBitmap
                if (bmp != null) {
                    Image(
                        bitmap = bmp.asImageBitmap(),
                        contentDescription = null,
                        contentScale = ContentScale.Crop,
                        modifier = Modifier.fillMaxSize(),
                    )
                } else {
                    Icon(
                        imageVector = Icons.Default.Headphones,
                        contentDescription = null,
                        tint = colors.textSecondary,
                        modifier = Modifier.size(20.dp),
                    )
                }
            }
            Spacer(Modifier.width(12.dp))
            // Title / artist
            Column(
                modifier = Modifier.weight(1f),
                verticalArrangement = Arrangement.Center,
            ) {
                Text(
                    text = title ?: "Playing…",
                    color = colors.textPrimary,
                    fontSize = 13.sp,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                if (!artist.isNullOrBlank()) {
                    Text(
                        text = artist!!,
                        color = colors.textSecondary,
                        fontSize = 11.sp,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
            Spacer(Modifier.width(8.dp))
            // Play / Pause
            IconButton(onClick = {
                controller?.let { c ->
                    if (c.isPlaying) c.pause() else c.play()
                }
            }) {
                Icon(
                    imageVector = if (isPlaying) Icons.Default.Pause else Icons.Default.PlayArrow,
                    contentDescription = if (isPlaying) "Pause" else "Play",
                    tint = colors.textPrimary,
                )
            }
        }
        LinearProgressIndicator(
            progress = { progress.coerceIn(0f, 1f) },
            modifier = Modifier
                .fillMaxWidth()
                .height(2.dp),
            color = colors.accent,
            trackColor = colors.border,
        )
    }
}

/**
 * Helper: decode a MediaController mediaId ("pair_42" or "audiobook_17") into the
 * relevant integer id. Returns null when the id doesn't match the expected prefix.
 */
fun decodePairIdFromMediaId(mediaId: String): Int? =
    (MediaId.parse(mediaId) as? MediaId.Pair)?.pairId
