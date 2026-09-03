package com.booksync.player

import android.util.Log
import fi.iki.elonen.NanoHTTPD
import java.io.File
import java.io.IOException
import java.io.InputStream
import java.io.RandomAccessFile
import java.util.logging.Level

/**
 * Tiny LAN-only HTTP server that streams downloaded audiobook files to a Cast receiver.
 *
 * Why this exists: the Google Home Mini hardcodes its DNS to 8.8.8.8/8.8.4.4 and ignores
 * the DHCP-provided resolver, so it can't resolve a LAN-only hostname (the usual way to
 * reach a self-hosted Tandem) and fails the Cast LOAD before any HTTP request leaves the
 * speaker. Audiobooks are already on-device after download, so the phone can serve them
 * directly to the speaker over the Wi-Fi LAN — no DNS, no public exposure. This mirrors
 * how Audiobookshelf casts (direct file streaming with HTTP Range support, mimeType
 * audio/mp4).
 *
 * URL shape: `http://<phone-IP>:<port>/<pathToken>/<URL-encoded filename>`.
 *
 * Security: the URL is constrained to the same LAN. We add a per-session random `pathToken`
 * and reject any request that doesn't match it (cheap defense against an attacker who
 * happens to be on the same Wi-Fi and scans ports). The server only serves files inside
 * the given audiobooks directory — `..` and absolute paths are rejected.
 *
 * Range serving uses RandomAccessFile.seek (canonical, unambiguous) rather than
 * FileInputStream.channel.position so the bytes delivered always match the declared
 * Content-Range — critical for moov-at-end M4B files where the receiver fetches the
 * index from the end of the file before it can decode.
 */
class LocalCastHttpServer(
    /** Directory containing the downloaded audiobook files (typically `filesDir/audiobooks`). */
    private val audiobooksDir: File,
    /** Random opaque token that must appear as the first URL path segment. */
    private val pathToken: String,
    /** Host to bind to; null lets NanoHTTPD bind to 0.0.0.0. */
    hostname: String? = null,
) : NanoHTTPD(hostname, /* port = */ 0) {

    companion object {
        private const val TAG = "LocalCastHttpServer"

        init {
            // The Cast receiver legitimately aborts (RST) the open-ended `bytes=0-` probe once
            // it has read enough of the header, which makes NanoHTTPD log a noisy
            // "Could not send response to the client / Connection reset" stack every time.
            // Quiet its java.util.logging logger so logcat stays readable.
            java.util.logging.Logger.getLogger(NanoHTTPD::class.java.name).level = Level.OFF
        }
    }

    private val mimeByExtension = mapOf(
        "mp3" to "audio/mpeg",
        "m4a" to "audio/mp4",
        "m4b" to "audio/mp4",
        "flac" to "audio/flac",
        "ogg" to "audio/ogg",
        "wav" to "audio/wav",
        "aac" to "audio/aac",
    )

    override fun serve(session: IHTTPSession): Response {
        val rawUri = session.uri ?: return notFound("missing URI")
        val rangeHeader = session.headers["range"]
        Log.d(TAG, "serve: ${session.method} $rawUri range=${rangeHeader ?: "(none)"}")

        // Expect /<pathToken>/<filename>. NanoHTTPD already URL-decodes the path.
        val parts = rawUri.trimStart('/').split('/', limit = 2)
        if (parts.size != 2 || parts[0] != pathToken) {
            Log.w(TAG, "serve: rejected $rawUri (token mismatch or malformed)")
            return notFound("not found")
        }
        val filename = parts[1]
        if (filename.isEmpty() || filename.contains("..") || filename.contains('/')) {
            Log.w(TAG, "serve: rejected suspicious filename '$filename'")
            return notFound("not found")
        }

        val file = File(audiobooksDir, filename)
        if (!file.isFile) {
            Log.w(TAG, "serve: file missing for request '$filename'")
            return notFound("not found")
        }

        val mime = mimeByExtension[file.extension.lowercase()] ?: "application/octet-stream"
        return try {
            if (rangeHeader != null) {
                serveRange(file, mime, rangeHeader)
            } else {
                serveFull(file, mime)
            }
        } catch (e: IOException) {
            Log.e(TAG, "serve: I/O error reading ${file.name}", e)
            newFixedLengthResponse(Response.Status.INTERNAL_ERROR, MIME_PLAINTEXT, "io error")
        }
    }

    private fun serveFull(file: File, mime: String): Response {
        val length = file.length()
        val response = newFixedLengthResponse(
            Response.Status.OK,
            mime,
            offsetStream(file, 0L),
            length,
        )
        response.addHeader("Accept-Ranges", "bytes")
        response.addHeader("Content-Disposition", "inline")
        return response
    }

    private fun serveRange(file: File, mime: String, rangeHeader: String): Response {
        val fileLength = file.length()
        // Range: bytes=START-END (END optional → through end of file).
        val spec = rangeHeader.removePrefix("bytes=").trim()
        val dash = spec.indexOf('-')
        if (dash < 0) return rangeNotSatisfiable(fileLength)

        val start = spec.substring(0, dash).trim().toLongOrNull() ?: 0L
        val end = spec.substring(dash + 1).trim().let { suffix ->
            if (suffix.isEmpty()) fileLength - 1 else suffix.toLongOrNull() ?: (fileLength - 1)
        }.coerceAtMost(fileLength - 1)

        if (start < 0 || start > end || start >= fileLength) {
            return rangeNotSatisfiable(fileLength)
        }

        val length = end - start + 1
        val response = newFixedLengthResponse(
            Response.Status.PARTIAL_CONTENT,
            mime,
            offsetStream(file, start),
            length,
        )
        response.addHeader("Content-Range", "bytes $start-$end/$fileLength")
        response.addHeader("Accept-Ranges", "bytes")
        response.addHeader("Content-Disposition", "inline")
        return response
    }

    /**
     * Returns an InputStream that reads [file] starting at byte [start], backed by
     * RandomAccessFile.seek. Unlike FileInputStream.channel.position(), this is guaranteed
     * to deliver bytes from exactly [start] — so the response body always matches the
     * advertised Content-Range.
     */
    private fun offsetStream(file: File, start: Long): InputStream {
        val raf = RandomAccessFile(file, "r")
        raf.seek(start)
        return object : InputStream() {
            override fun read(): Int = raf.read()
            override fun read(b: ByteArray, off: Int, len: Int): Int = raf.read(b, off, len)
            override fun close() = raf.close()
        }
    }

    private fun rangeNotSatisfiable(fileLength: Long): Response {
        val response = newFixedLengthResponse(
            Response.Status.RANGE_NOT_SATISFIABLE,
            MIME_PLAINTEXT,
            "range not satisfiable",
        )
        response.addHeader("Content-Range", "bytes */$fileLength")
        return response
    }

    private fun notFound(message: String): Response =
        newFixedLengthResponse(Response.Status.NOT_FOUND, MIME_PLAINTEXT, message)
}
