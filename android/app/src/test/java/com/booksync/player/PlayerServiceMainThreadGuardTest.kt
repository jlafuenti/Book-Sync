package com.booksync.player

import java.io.File
import org.junit.Assert.assertFalse
import org.junit.Test

/**
 * Issue #435: `AudioPlayerService` answers session custom commands on the
 * main thread. `getChaptersBundle` used to open a `MediaMetadataRetriever` on
 * the current item's URI there, and for a *streamed* audiobook that URI is the
 * server's HTTPS media URL — a network read on the main thread that stalled
 * for a minute on a slow link and ended in "Timeout executing service" and an
 * ANR. The retriever's result was never even read. This pins that no metadata
 * retriever is constructed anywhere in the service; anything that needs one
 * belongs on `Dispatchers.IO` in a separate, testable class.
 */
class PlayerServiceMainThreadGuardTest {

    private fun serviceSource(): String {
        var dir = File("").absoluteFile
        repeat(5) {
            for (rel in listOf(
                "app/src/main/java/com/booksync/player/AudioPlayerService.kt",
                "src/main/java/com/booksync/player/AudioPlayerService.kt",
            )) {
                val f = File(dir, rel)
                if (f.exists()) return f.readText()
            }
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("AudioPlayerService.kt not found from ${File("").absolutePath}")
    }

    @Test
    fun `the service never opens a MediaMetadataRetriever on the session thread`() {
        val source = serviceSource()
        assertFalse(
            "AudioPlayerService must not construct MediaMetadataRetriever: session " +
                "custom commands run on the main thread and the item URI may be a " +
                "streamed HTTPS URL (issue #435)",
            source.contains("MediaMetadataRetriever"),
        )
    }
}
