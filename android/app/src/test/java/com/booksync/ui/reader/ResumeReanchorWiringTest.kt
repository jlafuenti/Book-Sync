package com.booksync.ui.reader

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Source guard for issue #682: `ReaderActivity` has no JVM-testable surface
 * of its own (it is excluded from kover, same as `AudioPlayerService` — see
 * `PauseOwnershipWiringTest`), so the wiring between its lifecycle callbacks
 * and [ResumeReanchorPolicy] is pinned by reading the source instead. The
 * policy's own decision logic is covered behaviourally by
 * [ResumeReanchorPolicyTest].
 */
class ResumeReanchorWiringTest {

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

    private val readerActivitySource by lazy { source("com/booksync/ui/reader/ReaderActivity.kt") }
    private val readerActivityLines by lazy { codeLines(readerActivitySource) }

    @Test
    fun `onStop records that the activity was backgrounded`() {
        val onStop = readerActivitySource.substringAfter("override fun onStop()").substringBefore("\n    }")
        assertTrue(
            "onStop must set the flag onStart consumes — without it a resume " +
                "can never be told apart from the very first onStart after onCreate.",
            onStop.contains("hasStoppedSinceOpen = true"),
        )
    }

    @Test
    fun `onStart consults the policy only after a real backgrounding, and only for a pair`() {
        val onStart = readerActivitySource.substringAfter("override fun onStart()").substringBefore("\n    }")
        assertTrue(
            "onStart must gate on the flag onStop set — a fresh open's first " +
                "onStart has nothing to re-anchor against.",
            onStart.contains("hasStoppedSinceOpen"),
        )
        assertTrue(
            "The flag must be consumed (reset), or every later onStart would " +
                "re-anchor again with nothing new having happened.",
            onStart.contains("hasStoppedSinceOpen = false"),
        )
        assertTrue(
            "A standalone (unpaired) ebook has no audiobook to have moved on " +
                "at all (issue #169) — onStart must not attempt to re-anchor one.",
            onStart.contains("isStandalone"),
        )
        assertTrue(
            "onStart must launch the re-anchor check.",
            onStart.contains("reanchorAfterResume()"),
        )
    }

    @Test
    fun `saves are blocked before the re-anchor decision runs and unblocked after`() {
        val onStart = readerActivitySource.substringAfter("override fun onStart()").substringBefore("\n    }")
        assertTrue(
            "onStart must arm the block before launching the coroutine that " +
                "decides whether to re-anchor — a save landing in the gap would " +
                "write exactly the stale page this exists to correct.",
            onStart.contains("savesBlockedForReanchor = true"),
        )
        assertTrue(
            "The unblock must run in a finally — a re-anchor that throws must " +
                "not leave saves permanently dropped.",
            onStart.contains("finally") && onStart.contains("savesBlockedForReanchor = false"),
        )
    }

    @Test
    fun `savePosition drops rather than queues a save while re-anchoring`() {
        val savePosition = readerActivitySource
            .substringAfter("private fun savePosition(locator: Locator) {")
            .substringBefore("\n    private fun ")
        val firstLines = savePosition.lines().take(12).joinToString("\n")
        assertTrue(
            "savePosition must check savesBlockedForReanchor before doing " +
                "anything else, so both the collector path and the onPause path " +
                "are covered by one guard.",
            firstLines.contains("savesBlockedForReanchor"),
        )
        assertTrue(
            "A blocked save must return without writing (dropped, not queued).",
            firstLines.contains("return"),
        )
    }

    @Test
    fun `reanchorAfterResume runs the shared ladder decision and navigates on a landing`() {
        val reanchor = readerActivitySource
            .substringAfter("private suspend fun reanchorAfterResume()")
            .substringBefore("\n    }\n")
        assertTrue(
            "The re-anchor decision must go through ResumeReanchorPolicy, not " +
                "some inline reimplementation of it.",
            reanchor.contains("ResumeReanchorPolicy.shouldReanchor("),
        )
        assertTrue(
            "A fresh fetch (issue #682) must reuse the same bounded prefetch " +
                "onCreate uses at open, not a raw unbounded call.",
            reanchor.contains("prefetchBeforeRestore("),
        )
        assertTrue(
            "A landing must reuse the ladder onCreate's own restore already " +
                "runs, not a second, divergent implementation.",
            reanchor.contains("getInitialLocator("),
        )
        assertTrue(
            "programmaticTarget must be set before navigating, same as every " +
                "other explicit navigator.go(...) call site — otherwise the " +
                "settle emission this produces is misread as a user page-turn.",
            reanchor.indexOf("programmaticTarget = target") in 0 until reanchor.indexOf("nav.go("),
        )
        assertTrue(
            "The save throttle must be reset after navigating, or an autosave " +
                "already due could fire before the settle is recognizable as an echo.",
            reanchor.contains("lastSaveTime = System.currentTimeMillis()"),
        )
    }

    /**
     * Issue #682 follow-up: [reanchorAfterResume] calls
     * [ReaderActivity.getInitialLocator] a second time, deliberately, to
     * react to a fresh fetch. `getInitialLocator` must not re-read the
     * "Switch to Reader" handoff extra on that second call — it describes
     * where the audiobook was at OPEN, not what the fresh fetch just found,
     * and re-prepending it every call would land every resume-after-listening
     * back at the original handoff spot: the same failure #682 reported,
     * through a second path.
     */
    @Test
    fun `getInitialLocator consumes the handoff anchor once, not on every call`() {
        val getInitialLocator = readerActivitySource
            .substringAfter("private suspend fun getInitialLocator(pub: Publication): Locator? {")
            .substringBefore("\n    private fun decodeHint")
        assertTrue(
            "getInitialLocator must read the handoff value through " +
                "HandoffAudioAnchor.consume(), not a fresh intent.getLongExtra " +
                "read — a raw read would return the same value on every call.",
            getInitialLocator.contains("handoffAudio.consume()"),
        )
        assertTrue(
            "The raw one-shot-unsafe read must be gone from this function, " +
                "not merely supplemented.",
            !getInitialLocator.contains("intent.getLongExtra(EXTRA_HANDOFF_AUDIO_MS"),
        )
        assertTrue(
            "handoffAudio must be seeded from the intent once, in onCreate — " +
                "the same place pairId and ebookId are read — not lazily or " +
                "per-call.",
            readerActivityLines.any {
                it.contains("handoffAudio = HandoffAudioAnchor(") && it.contains("EXTRA_HANDOFF_AUDIO_MS")
            },
        )
    }
}
