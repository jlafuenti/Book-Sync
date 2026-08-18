package com.booksync.ui.reader

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Provenance of the reader's visible-page text (issue #131).
 *
 * The in-WebView extractor has two paths that used to be indistinguishable to
 * the caller: a real DOM read of the current column, and `scrollBasedText` — a
 * slice of `body.innerText` at `bodyLength × scrollLeft / scrollWidth`. Because
 * Readium paginates with CSS columns, character offset does not track page
 * position, so that estimate routinely names text from another page. Returned as
 * a bare String it looked exactly like a real hit, and the handoff seeked to it
 * and told the user it had synced precisely.
 *
 * So the JS now reports which path answered, and this pins the parsing of that
 * report — including the malformed cases, where the safe reading is "not
 * precise" rather than "precise" (a wrong seek is worse than no seek).
 */
class VisibleTextTest {

    @Test
    fun `a dom read is precise`() {
        val v = VisibleText.parse("""{"text":"The bottom floor of Signet's building","source":"dom"}""")
        assertEquals("The bottom floor of Signet's building", v.text)
        assertTrue(v.isPrecise)
    }

    @Test
    fun `an estimate is not precise, even though it carries text`() {
        val v = VisibleText.parse("""{"text":"with broken walls on three of the four","source":"estimated"}""")
        assertEquals("with broken walls on three of the four", v.text)
        assertFalse(v.isPrecise)
    }

    @Test
    fun `empty text is never precise`() {
        assertFalse(VisibleText.parse("""{"text":"","source":"dom"}""").isPrecise)
    }

    @Test
    fun `a blank-only dom read is not precise`() {
        assertFalse(VisibleText.parse("""{"text":"   ","source":"dom"}""").isPrecise)
    }

    @Test
    fun `an unknown source is treated as an estimate`() {
        assertFalse(VisibleText.parse("""{"text":"something","source":"guess"}""").isPrecise)
    }

    @Test
    fun `malformed json yields no text and no precision`() {
        val v = VisibleText.parse("not json at all")
        assertEquals("", v.text)
        assertFalse(v.isPrecise)
    }

    @Test
    fun `null - what evaluateJavascript returns when the page is gone - is empty`() {
        val v = VisibleText.parse(null)
        assertEquals("", v.text)
        assertFalse(v.isPrecise)
    }

    @Test
    fun `tabs and newlines are flattened, since the matcher compares one line`() {
        val v = VisibleText.parse("""{"text":"first line\nsecond\tline","source":"dom"}""")
        assertEquals("first line second line", v.text)
    }

    @Test
    fun `the bridge's double encoding is decoded, not treated as one long string`() {
        // What evaluateJavascript actually delivers: our JSON.stringify(...)
        // re-encoded as a JSON string literal.
        val v = VisibleText.parse("\"{\\\"text\\\":\\\"page text\\\",\\\"source\\\":\\\"dom\\\"}\"")
        assertEquals("page text", v.text)
        assertTrue(v.isPrecise)
    }

    @Test
    fun `the literal string null - a JS return of null - is empty`() {
        assertEquals(VisibleText.EMPTY, VisibleText.parse("null"))
    }
}
