package com.booksync.player

import android.net.Uri
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import com.booksync.data.local.entity.BookPairEntity
import java.io.File

/**
 * The one way a pair's audiobook becomes a Media3 item (issue #762). Lifted
 * out of `PlayerViewModel.loadAudio` so the reader's read-along mode loads
 * exactly what the player would: same mediaId (which the service, Cast and
 * Android Auto resolve through [MediaId]), same source selection (local file
 * first, stream otherwise — [MediaSourceSelector]), same metadata.
 */
object PairMediaItems {
    /** The pair's MediaItem, or null when neither a local file nor a stream URL exists. */
    fun build(
        pair: BookPairEntity,
        localFile: File?,
        serverUrl: String,
        // Test seam: `Uri.parse` is a stub that returns null on the JVM, and
        // [toUri] rejects that. Production callers take the default.
        uriOf: (AudioSource) -> Uri? = { it.toUri() },
    ): MediaItem? {
        val source = MediaSourceSelector.select(localFile, serverUrl, pair.audiobookId) ?: return null
        val uri = uriOf(source)
        return MediaItem.Builder()
            .setMediaId(MediaId.Pair(pair.id).value)
            .setUri(uri)
            .setMediaMetadata(
                MediaMetadata.Builder()
                    .setTitle(pair.audiobookTitle)
                    .setArtist(pair.audiobookAuthor)
                    .build(),
            )
            .build()
    }
}
