package com.booksync.player

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Source guards for issue #226: one owner for the pause position write.
 *
 * `AudioPlayerService` is excluded from kover (Android service glue with no
 * JVM-testable surface), so — as with the heartbeat and the seek flush — the
 * ownership rule is pinned by reading the source. The behavioural half lives in
 * [PauseSavePolicyTest] and `PlayerViewModelPauseCommandTest`.
 */
class PauseOwnershipWiringTest {

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
    fun `the service declares, registers and handles CMD_USER_PAUSE`() {
        val service = codeLines(source("com/booksync/player/AudioPlayerService.kt"))
        assertTrue(
            "AudioPlayerService must declare, register (onConnect) and handle " +
                "(onCustomCommand) CMD_USER_PAUSE — the screen has no other way to " +
                "tell a deliberate pause from focus loss.",
            service.count { it.contains("CMD_USER_PAUSE") } >= 3,
        )
    }

    @Test
    fun `the pause listener takes its claimFormat from the policy`() {
        val service = codeLines(source("com/booksync/player/AudioPlayerService.kt"))
        assertTrue(
            "onIsPlayingChanged(false) must pass the PauseSavePolicy verdict as " +
                "claimFormat — a hard-coded false is what forced the screen to run " +
                "a second write of its own (issue #226).",
            service.any { it.contains("claimFormat = pauseSavePolicy.consumeClaimFormat()") },
        )
        assertTrue(
            "The involuntary stops must NOT consume the flag through some other " +
                "path: STATE_ENDED and disconnect stay claimFormat = false.",
            service.count { it.contains("claimFormat = false") } >= 2,
        )
    }

    @Test
    fun `the player screen announces its pauses and writes no position on them`() {
        val screen = source("com/booksync/ui/player/PlayerScreen.kt")
        val lines = codeLines(screen)

        assertTrue(
            "PlayerViewModel must send CMD_USER_PAUSE before pausing (togglePlayback " +
                "and stopAndSave).",
            lines.count { it.contains("CMD_USER_PAUSE") } >= 1,
        )

        // The 500 ms poll loop must no longer detect the pause edge and save:
        // that was the second write. It only mirrors state for the UI now.
        val pollLoop = screen.substringAfter("private fun startPositionPolling()")
            .substringBefore("fun ensureMediaLoaded()")
        assertTrue(
            "startPositionPolling must not call saveBookmark — AudioPlayerService's " +
                "onIsPlayingChanged is the single owner of the pause write " +
                "(issue #226).",
            !pollLoop.contains("saveBookmark"),
        )

        val stopAndSave = screen.substringAfter("fun stopAndSave()").substringBefore("\n    }")
        assertTrue(
            "stopAndSave must not call saveBookmark either — it announces the pause " +
                "and lets the service's listener do the one write.",
            !stopAndSave.contains("saveBookmark"),
        )
    }

    @Test
    fun `the teardown save does not append a second history entry`() {
        val screen = source("com/booksync/ui/player/PlayerScreen.kt")
        val onCleared = screen.substringAfter("override fun onCleared()").substringBefore("\n    }")
        assertTrue(
            "onCleared keeps its save (the controller may already be gone) but with " +
                "appendToLog = false — the pause that preceded it already logged the " +
                "boundary (issue #226).",
            onCleared.contains("appendToLog = false"),
        )
    }
}
