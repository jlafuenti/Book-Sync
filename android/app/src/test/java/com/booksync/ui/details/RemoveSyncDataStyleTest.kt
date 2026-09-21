package com.booksync.ui.details

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * "Remove sync data" (issue #678) deletes something from the device, so it is
 * drawn red like "Delete ebook" and "Delete pair". It shipped in the neutral
 * style on both screens, reading like a download action beside them.
 *
 * Compose is excluded from Kover, so this is a source guard, same approach as
 * [BookDetailsGatingWiringTest]: the row's own argument list must say
 * `destructive = true`.
 */
class RemoveSyncDataStyleTest {

    private fun source(rel: String): String {
        var dir = File("").absoluteFile
        repeat(4) {
            val candidate = File(dir, "app/src/main/java/$rel")
            if (candidate.exists()) return candidate.readText()
            val direct = File(dir, "src/main/java/$rel")
            if (direct.exists()) return direct.readText()
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("Could not locate $rel from ${File("").absolutePath}")
    }

    /** The `ActionRow(` call whose arguments contain [title], up to its closing paren. */
    private fun rowCall(src: String, title: String): String {
        // The title can also appear in comments; take the occurrence that sits
        // inside an `ActionRow(` argument list, i.e. just after one.
        var titleAt = src.indexOf("\"$title\"")
        var start = -1
        while (titleAt >= 0) {
            val candidate = src.lastIndexOf("ActionRow(", titleAt)
            if (candidate >= 0 && titleAt - candidate < 300 && !src.substring(candidate, titleAt).contains(")\n")) {
                start = candidate
                break
            }
            titleAt = src.indexOf("\"$title\"", titleAt + 1)
        }
        assertTrue("no ActionRow titled \"$title\" found", start >= 0)
        var depth = 0
        for (i in start until src.length) {
            when (src[i]) {
                '(' -> depth++
                ')' -> if (--depth == 0) return src.substring(start, i + 1)
            }
        }
        throw AssertionError("unterminated ActionRow for \"$title\"")
    }

    @Test
    fun `book details draws remove sync data as destructive`() {
        val row = rowCall(source("com/booksync/ui/details/BookDetailsScreen.kt"), "Remove sync data")
        assertTrue(row, row.contains("destructive = true"))
    }

    @Test
    fun `the card menu draws remove sync data as destructive`() {
        val row = rowCall(source("com/booksync/ui/components/CardOverflowMenu.kt"), "Remove sync data")
        assertTrue(row, row.contains("destructive = true"))
    }
}
