package com.booksync.auto

import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.media.MediaMetadataRetriever
import android.net.Uri
import android.util.Log
import androidx.core.content.FileProvider
import com.booksync.data.remote.ServerUrlManager
import com.booksync.data.remote.coverImageUrl
import com.booksync.player.CoverArtRung
import com.booksync.player.coverArtPlan
import dagger.hilt.android.qualifiers.ApplicationContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Manages cover art for Android Auto media items.
 *
 * Cover art is extracted from embedded M4B metadata on first access and cached to
 * filesDir/covers/{audiobookId}.jpg. Subsequent requests return the cached file.
 *
 * Returns content:// URIs via FileProvider so Android Auto (and other system processes)
 * can read the images without needing auth tokens.
 *
 * All public methods must be called from an IO coroutine — they perform disk I/O.
 */
@Singleton
class CoverArtHelper @Inject constructor(
    @param:ApplicationContext private val context: Context,
    // The same auth-capable client Retrofit and Coil use — the covers endpoint
    // needs a bearer token (issue #331).
    private val okHttpClient: OkHttpClient,
    private val serverUrlManager: ServerUrlManager,
) {
    companion object {
        private const val TAG = "CoverArtHelper"
        private const val COVERS_DIR = "covers"
        private const val AUDIOBOOKS_DIR = "audiobooks"
    }

    private val coversDir: File
        get() = File(context.filesDir, COVERS_DIR).also { it.mkdirs() }

    /**
     * Returns a content:// URI for the cover art of the given audiobook, or null if none is found.
     *
     * Walks [coverArtPlan]: the cached file, then artwork embedded in the audio
     * file, then the server's own cover.
     *
     * That third rung is issue #331. Without it the app only ever used embedded
     * art, so an audiobook whose file carries no `covr` atom or `APIC` frame was
     * blank in the player, the notification, the lock screen and Android Auto —
     * while every other screen showed the cover the server held for it. Because
     * all of those read this one cache, the rung fixes them together.
     *
     * [serverCoverPath] comes from `audiobooks.cover_path`; callers already hold
     * the pair or audiobook entity, which keeps this helper free of a repository
     * dependency.
     *
     * Must be called from an IO dispatcher.
     */
    fun getCoverUri(
        audiobookId: Int,
        audiobookFilename: String?,
        serverCoverPath: String? = null,
    ): Uri? {
        val coverFile = File(coversDir, "$audiobookId.jpg")
        val audioFile = audiobookFilename
            ?.let { File(File(context.filesDir, AUDIOBOOKS_DIR), it) }
            ?.takeIf { it.exists() }

        val plan = coverArtPlan(
            cachedExists = coverFile.exists(),
            audioFileExists = audioFile != null,
            serverCoverPath = serverCoverPath,
        )

        for (rung in plan) {
            val uri = when (rung) {
                is CoverArtRung.Cached -> uriFor(coverFile)
                is CoverArtRung.Embedded -> audioFile?.let { extractAndCache(audiobookId, it, coverFile) }
                is CoverArtRung.Server -> fetchAndCache(audiobookId, rung.coverPath, coverFile)
            }
            if (uri != null) return uri
        }
        return null
    }

    /**
     * Download the server's cover and cache it beside the extracted ones, so the
     * media session, the notification and Android Auto all see it (issue #331).
     *
     * Written through a temp file and renamed: a half-written JPEG left by a
     * dropped connection would otherwise be cached forever, since the cache is
     * keyed on existence alone.
     */
    private fun fetchAndCache(audiobookId: Int, coverPath: String, coverFile: File): Uri? {
        val url = coverImageUrl(serverUrlManager.currentUrl, coverPath) ?: return null
        val tmp = File(coverFile.parentFile, "${coverFile.name}.part")
        return try {
            okHttpClient.newCall(Request.Builder().url(url).build()).execute().use { response ->
                if (!response.isSuccessful) {
                    Log.w(TAG, "Cover fetch for audiobook $audiobookId: HTTP ${response.code}")
                    return null
                }
                val body = response.body ?: return null
                tmp.outputStream().use { out -> body.byteStream().copyTo(out) }
            }
            if (tmp.length() == 0L) {
                tmp.delete()
                return null
            }
            if (!tmp.renameTo(coverFile)) {
                tmp.delete()
                return null
            }
            Log.d(TAG, "Cached server cover for audiobook $audiobookId")
            uriFor(coverFile)
        } catch (e: Exception) {
            Log.w(TAG, "Failed to fetch server cover for audiobook $audiobookId", e)
            tmp.delete()
            null
        }
    }

    /**
     * Drop the cached cover so the next request re-resolves it (issue #331).
     *
     * The cache is keyed by audiobook id with no version, so a cover replaced on
     * the server would otherwise never be picked up. Call this when the
     * audiobook is deleted or re-downloaded — that covers the case where the
     * user actually did something about the art. A cover changed server-side
     * with no local action still goes stale; fixing that needs a version or
     * ETag on the record, which is a separate change.
     */
    fun invalidate(audiobookId: Int) {
        File(coversDir, "$audiobookId.jpg").delete()
    }

    /**
     * Grants Android Auto read access to a cover URI.
     * Call this before passing the URI in a MediaItem if cover art doesn't load automatically.
     */
    fun grantAutoReadPermission(coverUri: Uri) {
        try {
            context.grantUriPermission(
                "com.google.android.projection.gearhead",
                coverUri,
                Intent.FLAG_GRANT_READ_URI_PERMISSION
            )
        } catch (e: Exception) {
            Log.w(TAG, "Could not grant Auto read permission for $coverUri", e)
        }
    }

    private fun extractAndCache(audiobookId: Int, audioFile: File, coverFile: File): Uri? {
        return try {
            val retriever = MediaMetadataRetriever()
            retriever.setDataSource(audioFile.absolutePath)
            val artBytes = retriever.embeddedPicture
            retriever.release()

            if (artBytes == null) {
                Log.d(TAG, "No embedded art in ${audioFile.name}")
                return null
            }

            val bitmap = BitmapFactory.decodeByteArray(artBytes, 0, artBytes.size) ?: return null
            coverFile.outputStream().use { out ->
                bitmap.compress(Bitmap.CompressFormat.JPEG, 90, out)
            }
            bitmap.recycle()

            Log.d(TAG, "Cached cover for audiobook $audiobookId")
            uriFor(coverFile)
        } catch (e: Exception) {
            Log.w(TAG, "Failed to extract cover for audiobook $audiobookId", e)
            null
        }
    }

    private fun uriFor(file: File): Uri =
        FileProvider.getUriForFile(context, "${context.packageName}.fileprovider", file)
}

/**
 * Extract the embedded cover art from an audio file, downsampled to roughly
 * [maxPx] (issue #161). Blocking media/disk work — call from Dispatchers.IO.
 * The retriever is released in `finally`: the old inline copies in
 * PlayerScreen skipped release() whenever setDataSource threw.
 */
fun extractEmbeddedArt(audioFile: File, maxPx: Int = 1024): Bitmap? {
    val retriever = MediaMetadataRetriever()
    return try {
        retriever.setDataSource(audioFile.absolutePath)
        retriever.embeddedPicture?.let { decodeEmbeddedArt(it, maxPx) }
    } catch (_: Exception) {
        null
    } finally {
        try { retriever.release() } catch (_: Exception) {}
    }
}

/**
 * Decode embedded art without ever materialising the full-size bitmap: a
 * bounds-only pass, then a sampled decode via [coverArtInSampleSize]. A
 * 3000 px cover decoded at full size is ~36 MB of bitmap for a screen slot a
 * fraction of that — and the player used to do it on the main thread.
 */
fun decodeEmbeddedArt(bytes: ByteArray, maxPx: Int): Bitmap? {
    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
    val opts = BitmapFactory.Options().apply {
        inSampleSize = coverArtInSampleSize(bounds.outWidth, bounds.outHeight, maxPx)
    }
    return BitmapFactory.decodeByteArray(bytes, 0, bytes.size, opts)
}

/**
 * Standard power-of-two sampling (Android docs shape): the largest
 * inSampleSize that keeps BOTH dimensions at least [maxPx], so the decoded
 * bitmap never undershoots the target size. Pure math so the JVM suite can
 * pin it — BitmapFactory itself is stubbed on the JVM.
 */
internal fun coverArtInSampleSize(width: Int, height: Int, maxPx: Int): Int {
    var inSampleSize = 1
    if (height > maxPx || width > maxPx) {
        val halfHeight = height / 2
        val halfWidth = width / 2
        while (halfHeight / inSampleSize >= maxPx && halfWidth / inSampleSize >= maxPx) {
            inSampleSize *= 2
        }
    }
    return inSampleSize
}
