package com.booksync.ui.reader

import com.booksync.R
import com.booksync.SyncState
import com.booksync.data.repository.BookSyncRepository
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import kotlinx.coroutines.test.runTest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * The decision half of the reader's text-selection toolbar (issue #227).
 *
 * The WebView menu surgery itself — intercepting the floating ActionMode at
 * creation, wrapping its callback — is Android glue in
 * [ReaderSelectionController] and stays out of the JVM suite. What it
 * *decides* is pinned here: what the injected JavaScript reports, how the
 * bridge's answer is decoded, which toolbar items count as noise, which
 * word "Define" looks up, and what a "Sync to Audio" on a selection writes.
 */
class ReaderSelectionTest {

    private val repository = mockk<BookSyncRepository>(relaxed = true)

    @Before
    fun clearPendingSeek() { SyncState.pendingAudioSeekMs = -1L }

    @After
    fun resetPendingSeek() { SyncState.pendingAudioSeekMs = -1L }

    // ------------------------------------------------------------ the JS

    @Test
    fun `the tracker stores selections longer than three characters on the top window`() {
        // Readium serves chapters from same-origin iframes, and the ActionMode
        // clears the DOM selection before our menu item can read it — so the
        // tracker has to listen for selectionchange in every frame and park
        // the text somewhere that survives.
        assertTrue(SELECTION_TRACKER_JS.contains("'selectionchange'"))
        assertTrue(SELECTION_TRACKER_JS.contains("window.top._bookSyncSelection = text"))
        assertTrue(SELECTION_TRACKER_JS.contains("text.length > 3"))
        assertTrue("installs into existing iframes", SELECTION_TRACKER_JS.contains("querySelectorAll('iframe')"))
        assertTrue("and into iframes added later", SELECTION_TRACKER_JS.contains("MutationObserver"))
        assertTrue("idempotent per document", SELECTION_TRACKER_JS.contains("_bsListenerAdded"))
    }

    @Test
    fun `the capture prefers the parked selection and only then asks the frames`() {
        val parked = CAPTURE_SELECTION_JS.indexOf("window._bookSyncSelection")
        val frames = CAPTURE_SELECTION_JS.indexOf("contentWindow.getSelection()")
        val top = CAPTURE_SELECTION_JS.indexOf("window.getSelection().toString()")
        assertTrue(parked >= 0 && frames > parked && top > frames)
        assertTrue(CAPTURE_SELECTION_JS.contains("stored.trim().length > 3"))
    }

    // -------------------------------------------------- decoding the bridge

    @Test
    fun `a captured selection comes back as a quoted JSON string and is unwrapped`() {
        assertEquals("the harbour lay still", decodeCapturedSelection("\"the harbour lay still\""))
    }

    @Test
    fun `newlines inside a captured selection become spaces`() {
        assertEquals("one two", decodeCapturedSelection("\"one\\ntwo\""))
    }

    @Test
    fun `an empty or absent capture decodes to nothing`() {
        assertEquals("", decodeCapturedSelection("\"\""))
        assertEquals("", decodeCapturedSelection("\"   \""))
        assertEquals("", decodeCapturedSelection(null))
    }

    // ------------------------------------------------------- trimming noise

    private fun item(id: Int, title: String?) = SelectionMenuItem(id, title)

    @Test
    fun `system share, select-all and the text-classifier slot are stripped by id`() {
        val noise = selectionNoiseItemIds(
            listOf(
                item(android.R.id.shareText, "Anything"),
                item(android.R.id.selectAll, "Anything"),
                item(android.R.id.textAssist, "Anything"),
            ),
        )
        assertEquals(setOf(android.R.id.shareText, android.R.id.selectAll, android.R.id.textAssist), noise.toSet())
    }

    @Test
    fun `OEM items are stripped by title, loosely and case-insensitively`() {
        val items = listOf(
            item(1001, "Share…"),
            item(1002, "Translate"),
            item(1003, "Web search"),
            item(1004, "Search web"),
            item(1005, "SELECT ALL"),
            item(1006, "Assist"),
        )
        assertEquals(items.map { it.id }, selectionNoiseItemIds(items))
    }

    @Test
    fun `copy, our own actions, accessibility items and unknowns are left alone`() {
        val kept = listOf(
            item(android.R.id.copy, "Copy"),
            item(R.id.action_define, "Define"),
            item(R.id.action_sync_selection, "Sync to Audio"),
            item(2001, "Read aloud"),
            item(2002, "Speak"),
            item(2003, "Paste"),
            item(2004, null),
        )
        assertTrue(selectionNoiseItemIds(kept).isEmpty())
    }

    @Test
    fun `our own items are kept even if an OEM title rule would match them`() {
        // The id check comes first: nothing we injected is ever stripped.
        assertTrue(selectionNoiseItemIds(listOf(item(R.id.action_define, "Define (assist)"))).isEmpty())
    }

    // ---------------------------------------------------------- Define word

    @Test
    fun `define looks up the first word of the selection, shorn of punctuation`() {
        assertEquals("Hello", firstDefinableToken("Hello, world"))
        assertEquals("ellipsis", firstDefinableToken("...ellipsis and more"))
        assertEquals("well-known", firstDefinableToken("well-known phrase"))
        assertEquals("harbour", firstDefinableToken("  harbour lay still  "))
    }

    @Test
    fun `define has nothing to look up for an empty or numeric selection`() {
        assertEquals("", firstDefinableToken(""))
        assertEquals("", firstDefinableToken("   "))
        assertEquals("", firstDefinableToken("1234 5678"))
    }

    // ------------------------------------------------------- Sync to Audio

    @Test
    fun `a selection under five characters is too short to sync`() {
        assertTrue(selectionTooShortToSync("abcd"))
        assertTrue(selectionTooShortToSync("  ab  "))
        assertFalse(selectionTooShortToSync("abcde"))
    }

    @Test
    fun `a matched selection is written through the page handoff and reports the second`() = runTest {
        coEvery { repository.epubToAudioText(84, 16, "the harbour lay still") } returns 1_234_000

        val outcome = syncSelectionToAudio(
            repository = repository,
            pairId = 84,
            chapterIndex = 16,
            locatorJson = """{"href":"/ch16.xhtml"}""",
            selectedText = "the harbour lay still",
        )

        assertEquals(SelectionSyncOutcome.Matched(1_234_000), outcome)
        assertEquals(1_234_000L, SyncState.pendingAudioSeekMs)
        coVerify(exactly = 1) {
            repository.updateBookmark(
                pairId = 84,
                source = "ebook",
                epubChapter = 16,
                audioPositionMs = 1_234_000,
                epubLocator = """{"href":"/ch16.xhtml"}""",
                locatorAudioMs = 1_234_000,
            )
        }
    }

    @Test
    fun `the in-flight flag is raised before the handoff is written`() = runTest {
        // A savePosition landing between the match and the write must already
        // see sentenceSyncPending, or it resolves its own sync-point guess
        // over the deliberate one.
        val events = mutableListOf<String>()
        coEvery { repository.epubToAudioText(any(), any(), any()) } returns 9_000
        coEvery {
            repository.updateBookmark(any(), any(), any(), any(), any(), any(), any(), any(), any(), any())
        } coAnswers { events += "write" }

        syncSelectionToAudio(repository, 1, 2, "{}", "long enough text") { events += "flag:$it" }

        assertEquals(listOf("flag:9000", "write"), events)
    }

    @Test
    fun `an unmatched selection never raises the flag`() = runTest {
        coEvery { repository.epubToAudioText(any(), any(), any()) } returns 0
        var flagged = false

        syncSelectionToAudio(repository, 1, 2, "{}", "long enough text") { flagged = true }

        assertFalse(flagged)
    }

    @Test
    fun `an unmatched selection writes nothing`() = runTest {
        coEvery { repository.epubToAudioText(any(), any(), any()) } returns 0

        val outcome = syncSelectionToAudio(repository, 84, 16, "{}", "no such sentence in the map")

        assertEquals(SelectionSyncOutcome.NoMatch, outcome)
        assertEquals(-1L, SyncState.pendingAudioSeekMs)
        coVerify(exactly = 0) { repository.updateBookmark(any(), any(), any(), any(), any(), any(), any(), any(), any(), any()) }
    }

    @Test
    fun `the match is looked up in the chapter the reader is showing`() = runTest {
        coEvery { repository.epubToAudioText(any(), any(), any()) } returns 0

        syncSelectionToAudio(repository, 7, 3, "{}", "some selected words")

        coVerify(exactly = 1) { repository.epubToAudioText(7, 3, "some selected words") }
    }
}
