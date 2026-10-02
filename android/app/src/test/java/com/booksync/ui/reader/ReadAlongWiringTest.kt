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

    // ============ Walkthrough anchor for Follow audio (issue #764) ============

    @Test
    fun `the Follow audio tour anchor is published from the toolbar item and tracks the bars`() {
        val register = activity.substringAfter("private fun registerFollowAudioAnchor()").substringBefore("\n    }")
        assertTrue(register.contains("toolbar.post {"))
        assertTrue(register.contains("toolbar.findViewById<View>(R.id.action_read_along) ?: return@post"))
        assertTrue(register.contains("tourRegistry.set(TourAnchor.ReaderFollowAudio, itemView.windowRect())"))

        val bars = activity.substringAfter("private fun setBarsVisible(visible: Boolean)").substringBefore("\n    }")
        val shown = bars.substringBefore("} else {")
        val hidden = bars.substringAfter("} else {")
        assertTrue("shown bars publish the anchor", shown.contains("registerFollowAudioAnchor()"))
        assertTrue("hidden bars clear it", hidden.contains("tourRegistry.clear(TourAnchor.ReaderFollowAudio)"))
    }

    @Test
    fun `the Follow audio tour anchor is cleared when the reader is destroyed`() {
        val destroy = activity.substringAfter("override fun onDestroy()")
        assertTrue(destroy.contains("tourRegistry.clear(TourAnchor.ReaderFollowAudio)"))
    }

    // ============ Read along from a selection (issue #772) ============

    private val selectionController by lazy { source("com/booksync/ui/reader/ReaderSelectionController.kt") }
    private val readAlongSource by lazy { source("com/booksync/ui/reader/ReadAlongController.kt") }

    @Test
    fun `the selection toolbar host has a Read along action`() {
        val host = selectionController.substringAfter("interface Host {").substringBefore("private var hasInstalledInterceptor")
        assertTrue(host.contains("fun onReadAlong(dismiss: () -> Unit)"))
        assertTrue(
            "the activity must implement it",
            activity.contains("override fun onReadAlong(dismiss: () -> Unit) = readAlongFromSelection(dismiss)"),
        )
    }

    @Test
    fun `the Read along item sits after Sync to Audio, under the same availability condition`() {
        val inject = selectionController.substringAfter("private fun injectCustomItems(").substringBefore("/** Remove the noise")
        assertTrue(
            inject.contains(
                "if (host.syncToAudioAvailable && menu.findItem(R.id.action_read_along_selection) == null)",
            ),
        )
        assertTrue(inject.contains("menu.add(0, R.id.action_read_along_selection, 2, \"Read along\")"))
        assertTrue(inject.contains("host.onReadAlong { mode.finish() }"))
    }

    @Test
    fun `reading along from a selection looks the audio up with no rewind and leaves the handoff alone`() {
        val body = activity.substringAfter("private fun readAlongFromSelection(").substringBefore("\n    }")
        assertTrue(body.contains("navigator?.currentSelection()"))
        assertTrue(body.contains("selectionTooShortToSync(selectedText)"))
        assertTrue(body.contains("repository.epubToAudioText(pairId, chapterIndex, selectedText, rewindMs = 0)"))
        assertTrue("no match keeps the existing toast", body.contains("No matching audio found"))
        assertTrue("a match goes through the readiness gate", body.contains("requestFollowing(FollowStart.AudioMs(audioMs))"))
        assertTrue(
            "the handoff writes a bookmark and arms a pending seek the player would act on later",
            !body.contains("syncSelectionToAudio(") && !body.contains("PageAudioHandoff"),
        )
        assertTrue("the reader stays open", !body.contains("switchToAudio()"))
    }

    @Test
    fun `dismiss is called only after the selection has been read`() {
        val body = activity.substringAfter("private fun readAlongFromSelection(").substringBefore("\n    }")
        assertTrue(body.indexOf("navigator?.currentSelection()") < body.indexOf("dismiss()"))
    }

    @Test
    fun `every follow entry point shares one start path keyed by a sealed FollowStart`() {
        assertTrue(readAlongSource.contains("sealed interface FollowStart"))
        assertTrue(readAlongSource.contains("object KeepAudio : FollowStart"))
        assertTrue(readAlongSource.contains("object VisiblePage : FollowStart"))
        assertTrue(readAlongSource.contains("data class AudioMs(val ms: Int) : FollowStart"))
        assertTrue(activity.contains("private fun requestFollowing(start: FollowStart)"))
        assertTrue(activity.contains("private fun startFollowing(start: FollowStart)"))
        assertTrue(!activity.contains("seekToPage"))
        // The three callers: the player handoff keeps the audio where it is,
        // the toolbar toggle starts at the visible page.
        assertTrue(activity.contains("requestFollowing(FollowStart.KeepAudio)"))
        val toggle = activity.substringAfter("private fun toggleReadAlong()").substringBefore("\n    }")
        assertTrue(toggle.contains("requestFollowing(FollowStart.VisiblePage)"))
    }

    @Test
    fun `an explicit audio position is sought to with the rewind skipped, even while already following`() {
        val start = activity.substringAfter("private fun startFollowing(").substringBefore("\n    }")
        assertTrue(start.contains("is FollowStart.AudioMs -> start.ms"))
        assertTrue(start.contains("FollowStart.KeepAudio -> 0"))
        assertTrue(start.contains("readAlong.start(System.currentTimeMillis())"))
        assertTrue("a fresh start hides the Back to audio chip", start.contains("showBackToAudio(false)"))
        assertTrue("an earlier suspect probe must not pause the new run", start.contains("suspectVerifyJob?.cancel()"))
        assertTrue("it never toggles following off", !start.contains("stopFollowing()"))
    }

    @Test
    fun `the toolbar toggle and the selection entry share the readiness gate`() {
        val gate = activity.substringAfter("private fun requestFollowing(").substringBefore("\n    }")
        assertTrue(gate.contains("repository.readiness(pairId)"))
        assertTrue(gate.contains("startFollowing(start)"))
    }

    // ============ No stacked rewinds (issue #772) ============

    @Test
    fun `a page-anchored start asks for the audio position with no rewind of its own`() {
        val start = activity.substringAfter("private fun startFollowing(").substringBefore("\n    }")
        assertTrue(
            "epubToAudioText subtracts RESUME_REWIND_MS by default and the player's resume " +
                "rewind would take another 5s off the same start",
            start.contains("repository.epubToAudioText(pairId, chapterIndex, visible.text, rewindMs = 0)"),
        )
    }

    @Test
    fun `the skip-rewind command goes out before the seek and the play`() {
        val start = activity.substringAfter("private fun startFollowing(").substringBefore("\n    }")
        val command = start.indexOf("AudioPlayerService.CMD_SUPPRESS_NEXT_RESUME_REWIND")
        val seek = start.indexOf("ctrl.seekTo(")
        val play = start.indexOf("ctrl.play()")
        assertTrue("startFollowing must send CMD_SUPPRESS_NEXT_RESUME_REWIND", command >= 0)
        assertTrue("... before it seeks", seek > command)
        assertTrue("... and before it plays", play > command)
        assertTrue(
            "the command is sent with a fresh Bundle(), never Bundle.EMPTY",
            start.contains("SessionCommand(AudioPlayerService.CMD_SUPPRESS_NEXT_RESUME_REWIND, Bundle())"),
        )
    }

    @Test
    fun `the reader's own play button and the other starts keep the resume rewind`() {
        val toggle = activity.substringAfter("private fun toggleReadAlongPlayback()").substringBefore("\n    }")
        assertTrue(!toggle.contains("CMD_SUPPRESS_NEXT_RESUME_REWIND"))
        val sync = activity.substringAfter("private fun syncSelectedTextToAudio(").substringBefore("private fun formatAudioTime")
        assertTrue(!sync.contains("CMD_SUPPRESS_NEXT_RESUME_REWIND"))
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
        assertTrue("the decorator must be asked to lay out again once the page settles", apply.contains("requestReadAlongDecorationLayout()"))
        val relayout = activity.substringAfter("private fun requestReadAlongDecorationLayout()").substringBefore("\n    }")
        assertTrue(relayout.contains("delay(READ_ALONG_SETTLE_MS)"))
        assertTrue(relayout.contains(".requestLayout()"))
        val echo = activity.substringAfter("ReadAlongController.LocatorVerdict.Echo -> {").substringBefore("return@collect")
        assertTrue("our own jump's echo re-lays the mark out too", echo.contains("requestReadAlongDecorationLayout()"))
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

    @Test
    fun `an unloaded audiobook is loaded at the start position, not sought afterwards`() {
        // Found on a phone (issue #772): with the audio service not running, the
        // reader loaded the item with no start position and sought right after;
        // the service resolved the start to zero and the seek was lost, so
        // Read along began at the top of the book.
        val start = activity.substringAfter("private fun startFollowing(").substringBefore("\n    }")
        val target = start.indexOf("val audioMs = when (start)")
        val plan = start.indexOf("planAudioStart(")
        val load = start.indexOf("PairMediaItems.build(")
        assertTrue("the target must be known before the item is loaded", target in 0 until plan && plan in 0 until load)
        assertTrue(start.contains("ctrl.setMediaItem(item, plan.startMs)"))
        assertTrue(start.contains("repository.getBookmark(pairId)?.audioPositionMs"))
    }

    @Test
    fun `the toolbar button stops the audio as well as the following`() {
        // Owner feedback on a phone (issue #772): pressing Follow audio while it
        // is on used to hide the controls and leave the audio running with
        // nothing on screen to stop it.
        val toggle = activity.substringAfter("private fun toggleReadAlong()").substringBefore("\n    }")
        assertTrue(toggle.contains("stopFollowingAndPause()"))
        val stop = activity.substringAfter("private fun stopFollowingAndPause()").substringBefore("\n    }")
        assertTrue("a deliberate pause is announced like the player's", stop.contains("AudioPlayerService.CMD_USER_PAUSE"))
        assertTrue(stop.contains("ctrl.pause()"))
        assertTrue(stop.contains("stopFollowing()"))
        assertTrue(
            "the pause must come before the controls go, or a failure leaves audio with no controls",
            stop.indexOf("ctrl.pause()") < stop.indexOf("stopFollowing()"),
        )
    }

    @Test
    fun `the end of the book stops following without a pause command`() {
        assertTrue(activity.contains("Player.STATE_ENDED -> stopFollowing()"))
    }
}
