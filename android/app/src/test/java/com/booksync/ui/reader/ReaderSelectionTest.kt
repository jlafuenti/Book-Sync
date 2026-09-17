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
 * The decision half of the reader's text-selection toolbar (issues #227, #582).
 *
 * The WebView menu surgery itself — intercepting the floating ActionMode at
 * creation, wrapping its callback — is Android glue in
 * [ReaderSelectionController] and stays out of the JVM suite. What it
 * *decides* is pinned here: which selection text Define is allowed to use,
 * which toolbar items count as noise, which word "Define" looks up (and how
 * it is normalised before the dictionary lookup), and what a "Sync to Audio"
 * on a selection writes.
 */
class ReaderSelectionTest {

    private val repository = mockk<BookSyncRepository>(relaxed = true)

    @Before
    fun clearPendingSeek() { SyncState.pendingAudioSeekMs = -1L }

    @After
    fun resetPendingSeek() { SyncState.pendingAudioSeekMs = -1L }

    // ----------------------------------------------- which selection Define uses

    @Test
    fun `define uses exactly the navigator's current selection, never an earlier one`() {
        // No selection-tracking JS and no cached field to fall back to (issue
        // #582): defineSelectionText only ever sees what currentSelection()
        // reports for the resource on screen right now.
        assertEquals("", defineSelectionText(null))
        assertEquals("", defineSelectionText(""))
        assertEquals("", defineSelectionText("   "))
    }

    @Test
    fun `a short current selection is returned as-is`() {
        assertEquals("cat", defineSelectionText("cat"))
        assertEquals("a", defineSelectionText("a"))
    }

    @Test
    fun `a current selection is trimmed but otherwise passed through`() {
        assertEquals("the harbour lay still", defineSelectionText("  the harbour lay still  "))
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

    // ------------------------------------- normalising the token (issue #582)

    @Test
    fun `a curly apostrophe folds to straight so contractions survive`() {
        assertEquals("don't", firstDefinableToken("don\u2019t"))
    }

    @Test
    fun `a curly or straight possessive is reduced to the base word`() {
        assertEquals("Ship", firstDefinableToken("Ship\u2019s cargo"))
        assertEquals("Ship", firstDefinableToken("Ship's cargo"))
    }

    @Test
    fun `a leading curly quote folds to an apostrophe and trailing punctuation is still trimmed`() {
        // The fold treats it like any other apostrophe \u2014 kept as part of the
        // word, consistent with a straight leading apostrophe today \u2014 while
        // the trailing comma and full stop are still shorn off as before.
        assertEquals("'Twas", firstDefinableToken("\u2018Twas, a night."))
    }

    @Test
    fun `soft hyphens and zero-width characters inside a word are removed`() {
        assertEquals("world", firstDefinableToken("wor\u00ADld"))
        assertEquals("world", firstDefinableToken("wor\u200Bld"))
        assertEquals("world", firstDefinableToken("wor\u200Cld"))
        assertEquals("world", firstDefinableToken("wor\u200Dld"))
        assertEquals("world", firstDefinableToken("wor\u2060ld"))
        assertEquals("world", firstDefinableToken("wor\uFEFFld"))
    }

    @Test
    fun `a non-breaking space splits words like any other whitespace`() {
        assertEquals("hello", firstDefinableToken("hello\u00A0world"))
        assertEquals("hello", firstDefinableToken("hello\u202Fworld"))
    }

    @Test
    fun `existing punctuation-trimming and empty cases still hold`() {
        assertEquals("Hello", firstDefinableToken("Hello, world"))
        assertEquals("well-known", firstDefinableToken("well-known phrase"))
        assertEquals("", firstDefinableToken(""))
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
