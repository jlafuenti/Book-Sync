package com.booksync.ui.reader

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Companion guard to issue #163, from live testing: once rotation stopped
 * closing the reader, a new path opened — Readium re-lays out its WebView on
 * a configuration change and re-emits `currentLocator` with a recomputed,
 * often chapter-top-quantized progression, and NO user input. The collector's
 * debounced save then wrote that regressed locator over the real anchor
 * (observed live: sentence 227 → sentence 0, pushed to the server).
 *
 * The fix: ReaderActivity records the moment of onConfigurationChanged, and
 * the locator collector treats any emission inside RELAYOUT_ECHO_WINDOW_MS
 * of it as a re-layout echo — adopted as the new programmatic target,
 * never saved.
 */
class ReaderRelayoutEchoTest {

    private fun source(): List<String> {
        var dir = File("").absoluteFile
        repeat(4) {
            val candidate = File(dir, "app/src/main/java/com/booksync/ui/reader/ReaderActivity.kt")
            if (candidate.exists()) return candidate.readText()
                .lines().map { it.trim() }.filter { !it.startsWith("//") && !it.startsWith("*") }
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("ReaderActivity.kt not found")
    }

    @Test
    fun `the reader marks configuration changes so re-layout emissions are not saved`() {
        val code = source()
        assertTrue(
            "ReaderActivity must override onConfigurationChanged and record the " +
                "moment — with android:configChanges declared (issue #163), " +
                "rotation no longer recreates the activity, and the re-layout " +
                "locator emission must be recognized as programmatic.",
            code.any { it.contains("override fun onConfigurationChanged") },
        )
        assertTrue(
            "onConfigurationChanged must record lastConfigChangeAtMs",
            code.any { it.contains("lastConfigChangeAtMs =") },
        )
        assertTrue(
            "the locator collector must consult RELAYOUT_ECHO_WINDOW_MS and skip " +
                "the save for emissions inside the window — the debounced save " +
                "otherwise writes the chapter-top locator over the real anchor.",
            code.any { it.contains("RELAYOUT_ECHO_WINDOW_MS") && !it.contains("const val") },
        )
        // Suppressing saves alone is NOT enough (verified live): the re-laid-out
        // VIEW genuinely sits at the top of the chapter, and the exit's onPause
        // save then persists that regression from navigator.currentLocator. The
        // activity must capture the pre-change locator and navigate back to it
        // once the re-layout settles.
        assertTrue(
            "onConfigurationChanged must capture the pre-change locator " +
                "(relayoutRestoreTarget) and re-anchor the view with " +
                "navigator.go(...) after the re-layout settles",
            code.any { it.contains("relayoutRestoreTarget =") } &&
                code.any { it.contains("relayoutRestoreTarget?.let") || it.contains("go(") && it.contains("relayoutRestore") },
        )
    }
}
