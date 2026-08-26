package com.booksync.player

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Source guards for issue #141, in the style of [com.booksync.SyncWiringTest]:
 * the media-id wire format has exactly one owner, [MediaId]. A fourth prefix
 * was invented once ("standalone_") precisely because building and parsing
 * were scattered as string literals across three files; these guards pin every
 * builder and dispatcher to the shared type so it cannot happen silently again.
 */
class MediaIdWiringTest {

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
    fun `no production code builds a standalone_ media id`() {
        for (file in listOf(
            "com/booksync/ui/player/PlayerScreen.kt",
            "com/booksync/player/AudioPlayerService.kt",
        )) {
            assertTrue(
                "$file must not set a \"standalone_\" media id — no dispatcher " +
                    "recognises that prefix, so every service-side position save " +
                    "silently no-ops (issue #141).",
                codeLines(source(file)).none { it.contains("setMediaId(\"standalone_") },
            )
        }
    }

    @Test
    fun `media ids are built through MediaId, not string literals`() {
        for (file in listOf(
            "com/booksync/ui/player/PlayerScreen.kt",
            "com/booksync/player/AudioPlayerService.kt",
        )) {
            val lines = codeLines(source(file))
            assertTrue(
                "$file must build media ids via MediaId.Pair(...).value / " +
                    "MediaId.Audiobook(...).value, not interpolated literals.",
                lines.none { it.contains("setMediaId(\"pair_") || it.contains("setMediaId(\"audiobook_") },
            )
        }
    }

    @Test
    fun `media ids are parsed through MediaId, not prefix string checks`() {
        for (file in listOf(
            "com/booksync/player/AudioPlayerService.kt",
            "com/booksync/ui/components/MiniPlayerBar.kt",
        )) {
            val lines = codeLines(source(file))
            assertTrue(
                "$file must dispatch on MediaId.parse(...), not raw " +
                    "startsWith/removePrefix string checks — a scattered parser is " +
                    "how the unknown-prefix bug went unnoticed (issue #141).",
                lines.none {
                    it.contains("startsWith(\"pair_\")") ||
                        it.contains("startsWith(\"audiobook_\")") ||
                        it.contains("removePrefix(\"pair_\")") ||
                        it.contains("removePrefix(\"audiobook_\")")
                },
            )
            assertTrue(
                "$file must reference MediaId.parse at least once.",
                lines.any { it.contains("MediaId.parse(") },
            )
        }
    }
}
