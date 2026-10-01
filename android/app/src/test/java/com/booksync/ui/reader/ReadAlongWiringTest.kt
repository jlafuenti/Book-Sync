package com.booksync.ui.reader

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

/** Source guard for read-along's wiring (issue #762); the logic is in [ReadAlongControllerTest]. */
class ReadAlongWiringTest {

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

    private val nav by lazy { source("com/booksync/ui/BookSyncNavigation.kt") }
    private val player by lazy { source("com/booksync/ui/player/PlayerScreen.kt") }
    private val screen by lazy { source("com/booksync/ui/reader/ReaderScreen.kt") }
    private val activity by lazy { source("com/booksync/ui/reader/ReaderActivity.kt") }

    @Test
    fun `the reader route carries a readAlong flag`() {
        assertTrue(nav.contains("readAlong={readAlong}"))
        assertTrue(nav.contains("navArgument(\"readAlong\") { type = NavType.BoolType; defaultValue = false }"))
    }

    @Test
    fun `the player's Read along entry navigates with the flag and does not pause`() {
        val block = player.substringBefore("\"Read along\"").substringAfterLast("IconButton(")
        assertTrue("Read along must not call stopAndSave — the audio keeps playing", !block.contains("stopAndSave"))
        assertTrue(block.contains("onReadAlong(positionMs)"))
        assertTrue(nav.contains("onReadAlong = { audioMs ->"))
        assertTrue(nav.contains("Routes.reader(pairId, audioMs, readAlong = true)"))
    }

    @Test
    fun `ReaderScreen forwards the flag as the activity extra`() {
        assertTrue(screen.contains("putExtra(ReaderActivity.EXTRA_READ_ALONG, true)"))
        assertTrue(activity.contains("const val EXTRA_READ_ALONG = \"readAlong\""))
    }

    private fun nearestAppDir(): File {
        var dir = File("").absoluteFile
        repeat(4) {
            if (File(dir, "src/main/res").exists()) return dir
            val app = File(dir, "app")
            if (File(app, "src/main/res").exists()) return app
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("Could not locate the app module from ${File("").absolutePath}")
    }

    @Test
    fun `the toolbar has a Follow audio item hidden for standalone ebooks`() {
        val menu = File(nearestAppDir(), "src/main/res/menu/reader_toolbar.xml").readText()
        assertTrue(menu.contains("android:id=\"@+id/action_read_along\""))
        assertTrue(menu.contains("android:title=\"Follow audio\""))
        val init = activity.substringAfter("private fun initViews()").substringBefore("initOverlayHost()")
        assertTrue(init.contains("toolbar.menu.findItem(R.id.action_read_along)?.isVisible = false"))
        assertTrue(init.contains("R.id.action_read_along -> { toggleReadAlong(); true }"))
    }

    @Test
    fun `saves are dropped while following so the service stays the only position writer`() {
        val save = activity.substringAfter("private fun savePosition(locator: Locator)").substringBefore("val pub = publication")
        assertTrue(
            "savePosition must return before resolving anything while readAlong.isFollowing — " +
                "a reader save here would flip the record to ebook-sourced every few seconds",
            save.contains("if (readAlong.isFollowing)"),
        )
    }

    @Test
    fun `the locator collector asks the controller before treating an emission as user navigation`() {
        val collector = activity.substringAfter("nav.currentLocator.collect { locator ->").substringBefore("val target = programmaticTarget")
        assertTrue(collector.contains("when (readAlong.onLocatorEmitted("))
        assertTrue(collector.contains("ReadAlongController.LocatorVerdict.Echo ->"))
        assertTrue(collector.contains("ReadAlongController.LocatorVerdict.Suspect ->"))
        assertTrue(collector.contains("verifySuspectedTurn()"))
    }

    @Test
    fun `a suspected manual turn is confirmed by the page before following pauses`() {
        val verify = activity.substringAfter("private fun verifySuspectedTurn()").substringBefore("\n    }")
        assertTrue("the probe must wait for the page to settle", verify.contains("delay(READ_ALONG_SETTLE_MS)"))
        assertTrue("only one probe per burst", verify.contains("suspectVerifyJob?.cancel()"))
        assertTrue(verify.contains("isSentenceVisible("))
        assertTrue(verify.contains("readAlong.onSuspectVerified(visible)"))
        assertTrue(verify.contains("showBackToAudio(readAlong.isPaused)"))
    }

    @Test
    fun `the page is asked for the preview's first line only`() {
        val locator = activity.substringAfter("private fun sentenceLocator(").substringBefore("\n    }")
        assertTrue(locator.contains("ReadAlongController.quoteFor(point.epubTextPreview)"))
        val decorate = activity.substringAfter("private fun onReadAlongDecorate(").substringBefore("\n    }")
        assertTrue(decorate.contains("ReadAlongController.quoteFor(point.epubTextPreview)"))
    }

    @Test
    fun `resume re-anchoring is skipped while following`() {
        val reanchor = activity.substringAfter("private suspend fun reanchorAfterResume()").substringBefore("val pub = publication")
        assertTrue(reanchor.contains("if (readAlong.isFollowing) return"))
    }

    @Test
    fun `the reader's pause button announces the pause like the player does`() {
        val pause = activity.substringAfter("private fun toggleReadAlongPlayback()").substringBefore("\n    }")
        assertTrue(pause.contains("AudioPlayerService.CMD_USER_PAUSE"))
        assertTrue(pause.contains("ctrl.pause()"))
        assertTrue("decide on playWhenReady, not the momentary isPlaying", pause.contains("if (ctrl.playWhenReady)"))
    }

    @Test
    fun `starting to follow plays only when the player is not already set to play`() {
        val start = activity.substringAfter("private fun startFollowing(").substringBefore("\n    }")
        assertTrue(start.contains("if (!ctrl.playWhenReady) ctrl.play()"))
        assertTrue(!start.contains("if (!ctrl.isPlaying) ctrl.play()"))
    }

    @Test
    fun `following starts from the handoff extra only once the sync map is ready`() {
        assertTrue(activity.contains("intent.getBooleanExtra(EXTRA_READ_ALONG, false)"))
        val start = activity.substringAfter("private fun requestFollowing(").substringBefore("\n    }")
        assertTrue(start.contains("repository.readiness(pairId)"))
        assertTrue(start.contains("pendingSwitchStatus.value = status"))
    }

    @Test
    fun `the poll runs only while started and releases the controller on destroy`() {
        assertTrue(activity.contains("repeatOnLifecycle(Lifecycle.State.STARTED)"))
        val destroy = activity.substringAfter("override fun onDestroy()").substringBefore("\n    }")
        assertTrue(destroy.contains("readAlongMediaController?.release()"))
    }

    @Test
    fun `playback ending stops following`() {
        assertTrue(activity.contains("Player.STATE_ENDED -> stopFollowing()"))
    }

    @Test
    fun `the follow, decorate and jump entry points keep the names the decoration PR builds on`() {
        listOf(
            "private fun startFollowing(",
            "private fun stopFollowing()",
            "private fun onReadAlongDecorate(",
            "private fun jumpToSentence(",
            "private suspend fun isSentenceVisible(",
            "private fun showBackToAudio(",
            "private fun applyReadAlongDecoration(",
            "private fun clearReadAlongDecoration()",
        ).forEach { signature ->
            assertTrue("ReaderActivity must declare $signature", activity.contains(signature))
        }
    }

    @Test
    fun `the current sentence is decorated in its own group and cleared on stop`() {
        val apply = activity.substringAfter("private fun applyReadAlongDecoration(").substringBefore("\n    }")
        assertTrue(apply.contains("Decoration("))
        assertTrue(apply.contains("applyDecorations(listOf(decoration), READ_ALONG_DECORATION_GROUP)"))
        assertTrue(apply.contains("ReadAlongStyle.UNDERLINE -> Decoration.Style.Underline("))
        assertTrue(apply.contains("ReadAlongStyle.HIGHLIGHT -> Decoration.Style.Highlight("))
        val clear = activity.substringAfter("private fun clearReadAlongDecoration()").substringBefore("\n    }")
        assertTrue(clear.contains("applyDecorations(emptyList(), READ_ALONG_DECORATION_GROUP)"))
    }

    @Test
    fun `a style change re-decorates the sentence being followed`() {
        assertTrue(activity.contains("readAlongSettings.setListener {"))
        val listener = activity.substringAfter("readAlongSettings.setListener {").substringBefore("\n        }")
        assertTrue(listener.contains("readAlong.currentPoint?.let { applyReadAlongDecoration(it) }"))
    }
}
