package com.booksync.player

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Source guard for issue #772: the one-shot "skip the next resume rewind"
 * command. `AudioPlayerService` is Android service glue with no JVM-testable
 * surface, so, as with `CMD_USER_PAUSE` ([PauseOwnershipWiringTest]), the
 * declare / register / handle triple is pinned by reading the source. The
 * behaviour itself is in [ResumeRewindPlayerTest].
 */
class ResumeRewindSkipWiringTest {

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

    private val service by lazy { codeLines(source("com/booksync/player/AudioPlayerService.kt")) }

    @Test
    fun `the service declares the command`() {
        assertTrue(
            service.any {
                it.contains("const val CMD_SUPPRESS_NEXT_RESUME_REWIND = \"SUPPRESS_NEXT_RESUME_REWIND\"")
            },
        )
    }

    @Test
    fun `the service registers the command for controllers in onConnect`() {
        assertTrue(
            "A command that is not added to the session's available commands is " +
                "rejected before onCustomCommand ever sees it.",
            service.any { it.contains(".add(SessionCommand(CMD_SUPPRESS_NEXT_RESUME_REWIND, Bundle.EMPTY))") },
        )
    }

    @Test
    fun `the service handles the command by arming the player's one-shot`() {
        val text = source("com/booksync/player/AudioPlayerService.kt")
        val handler = text.substringAfter("CMD_SUPPRESS_NEXT_RESUME_REWIND ->").substringBefore("CMD_")
        assertTrue(
            "The handler must arm ResumeRewindPlayer's one-shot and answer success.",
            handler.contains("resumeRewindPlayer?.suppressNextResumeRewind()") &&
                handler.contains("RESULT_SUCCESS"),
        )
    }

    @Test
    fun `the player exposes the one-shot`() {
        val player = source("com/booksync/player/ResumeRewindPlayer.kt")
        assertTrue(player.contains("fun suppressNextResumeRewind()"))
    }
}
