package com.booksync.player

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Source guards for issue #166: seek/skip must flush the position — the
 * contract lists them among the boundaries that "flush immediately, bypassing
 * the throttle" (the web debounces them ~1 s). Android flushed nothing, so a
 * scrub while paused was never saved on any path.
 *
 * The flush lives in the SERVICE (onPositionDiscontinuity), not the
 * PlayerViewModel — one owner, covering phone UI, Android Auto, notification
 * and headset seeks alike, exactly like the heartbeat and the resume rewind.
 */
class SeekFlushWiringTest {

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
    fun `the service flushes user seeks, debounced, with the programmatic exclusions`() {
        val service = codeLines(source("com/booksync/player/AudioPlayerService.kt"))

        assertTrue(
            "AudioPlayerService must implement onPositionDiscontinuity and key on " +
                "DISCONTINUITY_REASON_SEEK — otherwise seeks are never flushed (issue #166).",
            service.any { it.contains("onPositionDiscontinuity") } &&
                service.any { it.contains("DISCONTINUITY_REASON_SEEK") },
        )
        assertTrue(
            "The seek flush must be debounced (SEEK_FLUSH_DEBOUNCE_MS, mirroring the " +
                "web's AudioPlayerContext) so a scrub burst produces one save.",
            service.any { it.contains("SEEK_FLUSH_DEBOUNCE_MS") },
        )
        assertTrue(
            "The wrapper's own resume rewind must be excluded " +
                "(consumeResumeRewindSeek) — flushing it with claimFormat=true would " +
                "claim the format on a mere resume.",
            service.any { it.contains("consumeResumeRewindSeek(") },
        )
        assertTrue(
            "Seeks before playback has been heard (the restore seek at open) must be " +
                "excluded via hasPlayedSinceItemTransition.",
            service.any { it.contains("hasPlayedSinceItemTransition") },
        )
    }

    @Test
    fun `the restore seek is suppressed even when the has-played gate is open`() {
        // Found live: reopening the player while its item is still loaded means
        // no media-item transition, so hasPlayedSinceItemTransition is still
        // true from the previous session — and the RESTORE seek then passed
        // the gate and was flushed with claimFormat=true on a mere screen
        // open. The ViewModel now announces its programmatic seeks with
        // CMD_SUPPRESS_NEXT_SEEK_FLUSH before issuing them.
        val service = codeLines(source("com/booksync/player/AudioPlayerService.kt"))
        assertTrue(
            "AudioPlayerService must declare, register and handle " +
                "CMD_SUPPRESS_NEXT_SEEK_FLUSH",
            service.count { it.contains("CMD_SUPPRESS_NEXT_SEEK_FLUSH") } >= 3,
        )
        assertTrue(
            "onPositionDiscontinuity must consume suppressNextSeekFlush before " +
                "scheduling a flush",
            service.any { it.contains("suppressNextSeekFlush") && it.contains("if") } ||
                service.any { it.contains("consumeSuppressNextSeekFlush") },
        )

        val screen = codeLines(source("com/booksync/ui/player/PlayerScreen.kt"))
        val restoreSeekUses = screen.count { it.contains("restoreSeek(") }
        assertTrue(
            "PlayerScreen's four restore-seek sites (paired collect, standalone " +
                "restore, connect-time seek, pending-seek apply) must go through " +
                "restoreSeek(...) — found $restoreSeekUses reference(s), expected >= 5 " +
                "(4 call sites + the definition).",
            restoreSeekUses >= 5,
        )
    }

    @Test
    fun `the PlayerViewModel does not add a second flush — one owner`() {
        val screen = source("com/booksync/ui/player/PlayerScreen.kt")
        val seekToBlock = screen.lineSequence()
            .dropWhile { !it.contains("fun seekTo(") }
            .take(6)
            .joinToString("\n")
        assertTrue(
            "PlayerViewModel.seekTo must NOT call saveBookmark — the service's " +
                "onPositionDiscontinuity is the single owner of the seek flush, as " +
                "with the heartbeat (two owners double every write).",
            !seekToBlock.contains("saveBookmark"),
        )
    }
}
