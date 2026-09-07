package com.booksync.player

import androidx.media3.common.MediaItem
import androidx.media3.common.MimeTypes

/** Where the phone's LAN cast server is listening, for the life of one Cast session. */
data class CastServerAddress(val ip: String, val port: Int, val pathToken: String)

/**
 * What the Cast receiver is told to load (issue #225).
 *
 * Cast serves the *phone's* copy over the LAN (`LocalCastHttpServer`): the
 * receiver cannot send a Bearer header, so there is no server-side Cast path,
 * and a book that is only being streamed cannot be cast. The URL shape is the
 * server's contract — `http://<ip>:<port>/<token>/<encoded filename>` — with
 * the token as the first path segment and the filename, URL-decoded by
 * NanoHTTPD, as the second.
 *
 * Pure so it can be pinned without a Chromecast; `AudioPlayerService` (excluded
 * from Kover) only looks the book up and hands the pieces here.
 */
object CastMediaItemFactory {

    /**
     * Null when there is nothing the receiver could fetch: the LAN server is
     * not running, the book could not be resolved, or its audio is not on the
     * phone — handing the receiver the server URL instead would earn a 401 and
     * an idle television (issue #171), so refusing is the right answer.
     */
    fun build(
        original: MediaItem,
        address: CastServerAddress?,
        filename: String?,
        hasLocalFile: Boolean,
    ): MediaItem? {
        if (address == null || filename == null || !hasLocalFile) return null
        // Artwork is intentionally dropped — the receiver runs in a browser
        // context and cannot fetch a content:// URI, and there is no LAN cover
        // to substitute. The Default Media Receiver shows its own icon.
        return original.buildUpon()
            .setUri(streamUrl(address, filename))
            .setMimeType(mimeTypeFor(filename))
            .setMediaMetadata(
                original.mediaMetadata.buildUpon()
                    .setArtworkUri(null)
                    .build()
            )
            .build()
    }

    /** The token is already hex and is not encoded; the filename is. */
    fun streamUrl(address: CastServerAddress, filename: String): String =
        "http://${address.ip}:${address.port}/${address.pathToken}/${encodePathSegment(filename)}"

    fun mimeTypeFor(filename: String): String =
        when (filename.substringAfterLast('.', "").lowercase()) {
            "mp3" -> MimeTypes.AUDIO_MPEG
            "m4b", "m4a" -> MimeTypes.AUDIO_MP4
            "flac" -> MimeTypes.AUDIO_FLAC
            "ogg" -> MimeTypes.AUDIO_OGG
            "aac" -> MimeTypes.AUDIO_AAC
            "wav" -> "audio/wav"
            else -> MimeTypes.AUDIO_MPEG
        }

    /**
     * `android.net.Uri.encode(s)`, reimplemented: keep `[A-Za-z0-9_-!.~'()*]`,
     * percent-encode everything else as uppercase-hex UTF-8 bytes. Not
     * `URLEncoder` (form encoding: `+` for space) and not the platform `Uri`,
     * which is a stub on the JVM — this is the one place the rule lives so a
     * test can hold it.
     */
    fun encodePathSegment(segment: String): String {
        val out = StringBuilder(segment.length)
        for (byte in segment.toByteArray(Charsets.UTF_8)) {
            val c = byte.toInt() and 0xFF
            if (c < 0x80 && isUnreserved(c.toChar())) {
                out.append(c.toChar())
            } else {
                out.append('%').append(HEX[c shr 4]).append(HEX[c and 0x0F])
            }
        }
        return out.toString()
    }

    private const val HEX = "0123456789ABCDEF"

    private fun isUnreserved(c: Char): Boolean =
        c in 'A'..'Z' || c in 'a'..'z' || c in '0'..'9' || c in "_-!.~'()*"
}
