package com.booksync.auto

import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.media.MediaMetadataRetriever
import android.net.Uri
import android.util.Log
import androidx.core.content.FileProvider
import dagger.hilt.android.qualifiers.ApplicationContext
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
    @param:ApplicationContext private val context: Context
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
     * Lookup order:
     *  1. Cached file at filesDir/covers/{audiobookId}.jpg — returned immediately
     *  2. Embedded artwork extracted from the M4B at filesDir/audiobooks/{filename}
     *
     * Must be called from an IO dispatcher.
     */
    fun getCoverUri(audiobookId: Int, audiobookFilename: String?): Uri? {
        val coverFile = File(coversDir, "$audiobookId.jpg")
        if (coverFile.exists()) {
            return uriFor(coverFile)
        }
        if (audiobookFilename == null) return null
        val audioFile = File(File(context.filesDir, AUDIOBOOKS_DIR), audiobookFilename)
        if (!audioFile.exists()) return null
        return extractAndCache(audiobookId, audioFile, coverFile)
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
