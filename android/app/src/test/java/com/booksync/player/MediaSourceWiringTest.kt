package com.booksync.player

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Wiring guard for issue #171, in the same spirit as `SyncWiringTest`: the
 * selector can be perfectly correct and the app still refuse to stream, because
 * one `MediaItem` builder was left pointing straight at `Uri.fromFile(...)`.
 *
 * There were four such call sites — three in `AudioPlayerService` (the
 * Cast-to-local restore, the pair builder and the standalone builder) and one
 * pair in `PlayerViewModel` — and each one is a separate way to reach playback
 * (phone screen, Android Auto browse, notification resume, Cast handoff). None
 * of them is reachable from a JVM unit test: they need a `MediaController`, a
 * `MediaLibrarySession` or a live Cast session, and this module has no
 * Robolectric. So this reads the source. It pins the call, not the behaviour —
 * but "one builder forgot the selector" is exactly the failure it catches.
 */
class MediaSourceWiringTest {

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

    /** Comments don't count — a call site that became a comment is the bug. */
    private fun codeLines(text: String): List<String> =
        text.lines().map { it.trim() }.filter { !it.startsWith("//") && !it.startsWith("*") }

    /**
     * The body of a member function, from its `fun` line to the next member
     * declaration. Crude, but it is the difference between "the file mentions
     * the selector somewhere" and "this builder uses it".
     */
    private fun functionBody(text: String, name: String): String {
        val lines = text.lines()
        val start = lines.indexOfFirst { it.contains("fun $name(") }
        if (start < 0) throw AssertionError("No function named $name")
        val end = lines.drop(start + 1).indexOfFirst {
            Regex("""^\s{0,8}(private |internal |public |override )*(suspend )?fun \w""")
                .containsMatchIn(it)
        }
        val last = if (end < 0) lines.size else start + 1 + end
        return lines.subList(start, last).joinToString("\n")
    }

    private val service by lazy { source("com/booksync/player/AudioPlayerService.kt") }
    private val playerScreen by lazy { source("com/booksync/ui/player/PlayerScreen.kt") }

    @Test
    fun `every audio MediaItem builder in the service asks the selector`() {
        listOf("buildLocalMediaItem", "buildPairMediaItem", "buildAudiobookMediaItem").forEach { name ->
            assertTrue(
                "AudioPlayerService.$name must build its URI through " +
                    "MediaSourceSelector, or a book that is not downloaded is " +
                    "unplayable on that path (issue #171).",
                codeLines(functionBody(service, name)).any { it.contains("mediaUriFor") },
            )
        }
    }

    @Test
    fun `both player-screen loaders ask the selector`() {
        listOf("loadAudio", "loadStandaloneAudio").forEach { name ->
            assertTrue(
                "PlayerViewModel.$name must build its URI through " +
                    "MediaSourceSelector (issue #171).",
                codeLines(functionBody(playerScreen, name)).any { it.contains("mediaUriFor") },
            )
        }
    }

    @Test
    fun `no audio MediaItem is pinned to a local file behind the selector's back`() {
        listOf(
            "AudioPlayerService.kt" to service,
            "PlayerScreen.kt" to playerScreen,
        ).forEach { (label, text) ->
            val offenders = codeLines(text).filter { it.contains("Uri.fromFile(") }
            assertTrue(
                "$label still hands a file:// URI straight to a MediaItem: " +
                    "$offenders. Route it through MediaSourceSelector so an " +
                    "undownloaded book streams instead (issue #171).",
                offenders.isEmpty(),
            )
        }
    }

    @Test
    fun `the selector helper exists and is the one place the stream URL is built`() {
        val selector = source("com/booksync/player/MediaSourceSelector.kt")
        assertTrue(
            "MediaSourceSelector owns the /api/files/audiobook path.",
            selector.contains("api/files/audiobook"),
        )
        listOf(
            "AudioPlayerService.kt" to service,
            "PlayerScreen.kt" to playerScreen,
        ).forEach { (label, text) ->
            assertTrue(
                "$label must not hand-roll the stream URL — ask " +
                    "MediaSourceSelector, which normalises the server URL the " +
                    "same way retrofitBaseUrl does.",
                codeLines(text).none { it.contains("api/files/audiobook") },
            )
        }
    }

    @Test
    fun `cast still serves the phone's own copy`() {
        // LocalCastHttpServer is the entire Cast story: the receiver fetches the
        // downloaded file from the phone over the LAN. If buildCastMediaItem ever
        // started handing out the server's stream URL, the receiver would get a
        // 401 (it cannot send the Bearer header) and idle.
        val body = functionBody(service, "buildCastMediaItem")
        assertTrue(
            "buildCastMediaItem must keep pointing at the phone's LocalCastHttpServer.",
            body.contains("localCastIp") && body.contains("localCastPathToken"),
        )
        assertTrue(
            "buildCastMediaItem must still refuse when the file is not on the phone.",
            codeLines(body).any { it.contains("localAudioFile") },
        )
    }
}
