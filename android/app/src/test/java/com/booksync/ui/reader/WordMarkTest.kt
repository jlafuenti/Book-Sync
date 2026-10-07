package com.booksync.ui.reader

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The page scripts and the plan behind the read-along word mark (issue #836). */
class WordMarkTest {

    // --- the shared page-text search ----------------------------------------

    @Test
    fun `the visibility and locate scripts share one page search prelude`() {
        val visibility = sentenceVisibilityScript("A line.", null, null)
        val locate = wordMarkLocateScript("A line.", null, null, listOf(0..0, 2..5))
        assertTrue(visibility.contains(pageSearchPrelude))
        assertTrue(locate.contains(pageSearchPrelude))
        // The pieces both scripts depend on live in the prelude, once each.
        for (piece in listOf("function collapse(", "function squash(", "createTreeWalker", "function bestOccurrence(")) {
            assertEquals("prelude defines $piece once", 1, Regex(Regex.escape(piece)).findAll(pageSearchPrelude).count())
        }
    }

    @Test
    fun `the visibility script still searches the first sixty characters and answers the same three words`() {
        val js = sentenceVisibilityScript("q", "b", "a")
        assertTrue(js.contains("full.substring(0, 60)"))
        assertTrue(js.contains("'missing'"))
        assertTrue(js.contains("'visible'"))
        assertTrue(js.contains("'hidden'"))
        assertTrue(js.contains("(\"q\", \"b\", \"a\")"))
    }

    // --- locate -------------------------------------------------------------

    @Test
    fun `the locate script names the style, the highlight and the stored ranges`() {
        val js = wordMarkLocateScript("one two", null, null, listOf(0..2, 4..6))
        assertTrue(js.contains("tandem-word-style"))
        assertTrue(js.contains("::highlight(tandem-word)"))
        assertTrue(js.contains("rgba(255,193,7,0.67)"))
        assertTrue(js.contains("window.__tandemWordMark"))
        assertTrue(js.contains("CSS.highlights"))
    }

    @Test
    fun `the locate script embeds the token ranges as JSON and the strings escaped`() {
        val js = wordMarkLocateScript("say \"hi\" now", "line\\one", "two\nlines", listOf(0..2, 5..8, 10..12))
        assertTrue(js.contains("[[0,2],[5,8],[10,12]]"))
        assertTrue(js.contains("\"say \\\"hi\\\" now\""))
        assertTrue(js.contains("\"line\\\\one\""))
        assertTrue(js.contains("\"two\\nlines\""))
    }

    @Test
    fun `with no token ranges the array is empty`() {
        assertTrue(wordMarkLocateScript("x", null, null, emptyList()).contains(", [])"))
    }

    @Test
    fun `the locate script returns zero without the Custom Highlight API and searches the whole sentence`() {
        val js = wordMarkLocateScript("x", null, null, emptyList())
        assertTrue(js.contains("typeof CSS === 'undefined' || !CSS.highlights"))
        // Whole sentence, not the 60-character head the visibility probe uses.
        assertFalse(js.contains("substring(0, 60)"))
        assertTrue(js.contains("bestOccurrence(full, full.length"))
    }

    @Test
    fun `the style element is appended only once`() {
        val js = wordMarkLocateScript("x", null, null, emptyList())
        assertTrue(js.contains("if (!document.getElementById('tandem-word-style'))"))
    }

    // --- set and clear ------------------------------------------------------

    @Test
    fun `the set script draws one stored range under the highlight name, guarded`() {
        val js = wordMarkSetScript(3)
        assertTrue(js.contains("CSS.highlights.set('tandem-word', new Highlight(m.ranges[i]))"))
        assertTrue(js.contains("try"))
        assertTrue(js.contains("i >= m.ranges.length"))
        assertTrue(js.endsWith("(3)"))
    }

    @Test
    fun `the set script answers where the word sits relative to the viewport (issue 841)`() {
        val js = wordMarkSetScript(2)
        for (verdict in listOf("'visible'", "'right'", "'left'", "'none'")) {
            assertTrue("answers $verdict", js.contains(verdict))
        }
        // The same column test the visibility probe uses.
        assertTrue(js.contains("getClientRects()"))
        assertTrue(js.contains("width > 0"))
        assertTrue(js.contains("left >= -1"))
        assertTrue(js.contains("window.innerWidth"))
        assertTrue(js.contains("left >= vpW"))
        assertTrue(js.contains("right <= 0"))
    }

    @Test
    fun `the set script answers none when there is nothing to paint, and on any exception`() {
        val js = wordMarkSetScript(0)
        assertTrue(js.contains("!m || i < 0 || i >= m.ranges.length || !CSS.highlights) return 'none'"))
        assertTrue(js.contains("catch (e) { return 'none'; }"))
    }

    @Test
    fun `the clear script deletes the highlight, guarded`() {
        val js = wordMarkClearScript()
        assertTrue(js.contains("CSS.highlights.delete('tandem-word')"))
        assertTrue(js.contains("try"))
    }

    // --- plan ---------------------------------------------------------------

    private fun quote(highlight: String, before: String? = "prev", after: String? = "next") =
        SentenceQuote(before, highlight, after)

    @Test
    fun `a single line plan keeps the quote context and ranges the collapsed text`() {
        val plan = wordMarkPlan("  Alpha  beta\tgamma. ", quote("Alpha  beta\tgamma."))
        assertNotNull(plan)
        assertEquals("Alpha beta gamma.", plan!!.quote.highlight)
        assertEquals("prev", plan.quote.before)
        assertEquals("next", plan.quote.after)
        assertEquals(listOf(0..4, 6..9, 11..16), plan.tokenRanges)
    }

    @Test
    fun `a multi line preview searches the whole text and drops the after context`() {
        val plan = wordMarkPlan("First part,\nsecond part.", quote("First part,", after = "second part."))
        assertEquals("First part, second part.", plan!!.quote.highlight)
        assertEquals("prev", plan.quote.before)
        assertNull("the next line is inside the sentence, not after it", plan.quote.after)
        assertEquals(4, plan.tokenRanges.size)
    }

    @Test
    fun `a blank preview has no plan`() {
        assertNull(wordMarkPlan(null, quote("x")))
        assertNull(wordMarkPlan("  \n ", quote("x")))
    }
}
