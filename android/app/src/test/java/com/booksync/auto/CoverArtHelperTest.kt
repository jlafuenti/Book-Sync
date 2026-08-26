package com.booksync.auto

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Cover-art decode sampling (issue #161): the player used to decode embedded
 * art at full resolution on the UI thread — a visible freeze on every player
 * open with a large M4B, and a plausible ANR. The decode now runs on
 * Dispatchers.IO through [decodeEmbeddedArt], and [coverArtInSampleSize]
 * (pure math, tested here — BitmapFactory itself is a JVM stub) keeps a
 * 3000 px cover from being decoded at full size.
 *
 * Standard Android-docs power-of-two sampling: the largest inSampleSize that
 * keeps both dimensions at least maxPx, so quality never drops below the
 * target size.
 */
class CoverArtHelperTest {

    @Test
    fun `a large cover is downsampled`() {
        assertEquals(4, coverArtInSampleSize(width = 3000, height = 3000, maxPx = 512))
    }

    @Test
    fun `a small cover is not downsampled`() {
        assertEquals(1, coverArtInSampleSize(width = 400, height = 400, maxPx = 512))
    }

    @Test
    fun `sampling never undershoots either dimension`() {
        // 4000x1000 at maxPx 512: one halving already takes the height to 500,
        // below target — so no sampling is applied at all. Quality wins over
        // memory for asymmetric art; covers are square-ish in practice.
        assertEquals(1, coverArtInSampleSize(width = 4000, height = 1000, maxPx = 512))
    }

    @Test
    fun `degenerate bounds decode unsampled`() {
        assertEquals(1, coverArtInSampleSize(width = 0, height = 0, maxPx = 512))
    }

    @Test
    fun `the player extracts cover art through the helper, off the main thread`() {
        // Source guard, SyncWiringTest style: PlayerScreen used to inline the
        // MediaMetadataRetriever + full-size BitmapFactory decode on the main
        // thread, in two hand-copied blocks that also skipped release() on a
        // throw. The extraction now lives here (extractEmbeddedArt, with
        // try/finally release), and the ViewModel calls it on Dispatchers.IO.
        var dir = File("").absoluteFile
        var text: String? = null
        repeat(4) {
            val candidate = File(dir, "app/src/main/java/com/booksync/ui/player/PlayerScreen.kt")
            if (candidate.exists()) { text = candidate.readText(); return@repeat }
            dir = dir.parentFile ?: return@repeat
        }
        val code = (text ?: throw AssertionError("PlayerScreen.kt not found"))
            .lines().map { it.trim() }.filter { !it.startsWith("//") && !it.startsWith("*") }

        assertTrue(
            "PlayerScreen must not inline embedded-art extraction (embeddedPicture / " +
                "BitmapFactory.decodeByteArray) — it goes through extractEmbeddedArt " +
                "on Dispatchers.IO (issue #161).",
            code.none { it.contains("embeddedPicture") || it.contains("BitmapFactory.decodeByteArray") },
        )
        assertTrue(
            "PlayerScreen must call extractEmbeddedArt(...)",
            code.any { it.contains("extractEmbeddedArt(") },
        )
    }
}
