package com.booksync.player

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Source guards for issue #162: every playback-start path pulls the server
 * position first (contract § "Resume paths refresh first"), for BOTH scopes.
 * Paired books did this everywhere; standalone audiobooks did it nowhere in
 * the service — Android Auto and Cast resumption read only the local Room
 * projection, so a position set on the web resumed stale in the car, and the
 * first heartbeat then made the stale position canonical.
 *
 * [AudioPlayerService.refreshPositionBeforeResume] is scope-generic; these
 * pin that both the pair_ and audiobook_ branches of the two resume paths
 * (resolveMediaItem, Cast onPlaybackResumption) go through it. The
 * browse-tree builders deliberately do not (one request per row per browse).
 */
class ResumeRefreshWiringTest {

    private fun source(relativePath: String): String {
        var dir = File("").absoluteFile
        repeat(4) {
            val candidate = File(dir, "app/src/main/java/$relativePath")
            if (candidate.exists()) return candidate.readText()
            val direct = File(dir, "src/main/java/$relativePath")
            if (direct.exists()) return direct.readText()
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("Could not locate $relativePath from ${File("").absolutePath}")
    }

    private fun codeLines(text: String): List<String> =
        text.lines().map { it.trim() }.filter { !it.startsWith("//") && !it.startsWith("*") }

    @Test
    fun `both resume paths refresh the audiobook scope before seeking`() {
        val service = codeLines(source("com/booksync/player/AudioPlayerService.kt"))
        val audiobookRefreshes = service.count { it.contains("refreshPositionBeforeResume(\"audiobook\"") }
        assertTrue(
            "resolveMediaItem's and onPlaybackResumption's audiobook_ branches must " +
                "both call refreshPositionBeforeResume(\"audiobook\", …) — found " +
                "$audiobookRefreshes call site(s), expected 2 (issue #162).",
            audiobookRefreshes >= 2,
        )
    }

    @Test
    fun `both resume paths refresh the pair scope before seeking`() {
        val service = codeLines(source("com/booksync/player/AudioPlayerService.kt"))
        val pairRefreshes = service.count { it.contains("refreshPositionBeforeResume(\"pair\"") }
        assertTrue(
            "resolveMediaItem's and onPlaybackResumption's pair_ branches must both " +
                "call refreshPositionBeforeResume(\"pair\", …) — found " +
                "$pairRefreshes call site(s), expected 2.",
            pairRefreshes >= 2,
        )
    }
}
