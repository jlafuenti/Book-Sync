package com.booksync.ui.reader

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Issue #730, task 10. `LivePageProbe` builds the JS that reads the live Readium
 * WebView page/column position and parses the JSON it hands back, plus
 * `printListAt`, which mirrors web's `web/src/lib/printPages.js` `printListAt`
 * (task 7) so print-page-list resolution agrees between platforms.
 */
class LivePageProbeTest {

    // ---- script ----

    @Test
    fun `script embeds fragments as a JSON array literal`() {
        val js = LivePageProbe.script(listOf("p1", "p2"))
        assertEquals(true, js.contains("""["p1","p2"]"""))
        assertEquals(true, js.contains("FRAGMENTS"))
    }

    @Test
    fun `script escapes a fragment containing a quote and a backslash`() {
        val js = LivePageProbe.script(listOf("""a"b\c"""))
        // kotlinx.serialization must produce a valid JSON string literal, not raw
        // concatenation - the array literal contains an escaped quote and backslash.
        assertEquals(true, js.contains("""a\"b\\c"""))
        // And the substitution must not have been done by naive string concatenation
        // (which would break the JS with an unescaped quote).
        assertEquals(false, js.contains("""["a"b\c"]"""))
    }

    @Test
    fun `script contains the fixed template shape`() {
        val js = LivePageProbe.script(emptyList())
        assertEquals(true, js.contains("scrollingElement"))
        assertEquals(true, js.contains("innerWidth"))
        assertEquals(true, js.contains("JSON.stringify(out)"))
    }

    // ---- parse ----

    @Test
    fun `parse decodes the double-encoded JSON string form`() {
        val raw = """"{\"page\":2,\"total\":5,\"before\":[\"p1\",\"p2\"]}""""
        val result = LivePageProbe.parse(raw)
        assertEquals(LivePageProbe.Result(2, 5, setOf("p1", "p2")), result)
    }

    @Test
    fun `parse decodes a plain (non-double-encoded) JSON object`() {
        val raw = """{"page":1,"total":3,"before":[]}"""
        val result = LivePageProbe.parse(raw)
        assertEquals(LivePageProbe.Result(1, 3, emptySet()), result)
    }

    @Test
    fun `parse returns null for a null input`() {
        assertNull(LivePageProbe.parse(null))
    }

    @Test
    fun `parse returns null for the literal string null`() {
        assertNull(LivePageProbe.parse("null"))
    }

    @Test
    fun `parse returns null for empty input`() {
        assertNull(LivePageProbe.parse(""))
        assertNull(LivePageProbe.parse("   "))
    }

    @Test
    fun `parse returns null for malformed input`() {
        assertNull(LivePageProbe.parse("not json at all"))
        assertNull(LivePageProbe.parse("""{"page":"nope"}"""))
    }

    @Test
    fun `parse clamps page below 1 to null`() {
        val raw = """{"page":0,"total":5,"before":[]}"""
        assertNull(LivePageProbe.parse(raw))
    }

    @Test
    fun `parse clamps total below 1 to null`() {
        val raw = """{"page":1,"total":0,"before":[]}"""
        assertNull(LivePageProbe.parse(raw))
    }

    @Test
    fun `parse rejects page greater than total`() {
        val raw = """{"page":6,"total":5,"before":[]}"""
        assertNull(LivePageProbe.parse(raw))
    }

    @Test
    fun `parse accepts page equal to total`() {
        val raw = """{"page":5,"total":5,"before":["x"]}"""
        assertEquals(LivePageProbe.Result(5, 5, setOf("x")), LivePageProbe.parse(raw))
    }

    // ---- printListAt ----
    // Ported one for one from web/src/lib/printPages.test.js printListAt cases.

    private val pageList = listOf(1 to "a", 1 to "b", 3 to "c")
    private val labels = listOf("1", "2", "3")

    @Test
    fun `printListAt picks the last qualifying label across sections`() {
        val result = LivePageProbe.printListAt(pageList, labels, sectionIndex = 3, before = emptySet())
        assertEquals(ReaderProgress.PrintList("2", "1", "3"), result)
    }

    @Test
    fun `printListAt includes a same-section entry when its fragment is in before`() {
        val result = LivePageProbe.printListAt(pageList, labels, sectionIndex = 3, before = setOf("c"))
        assertEquals(ReaderProgress.PrintList("3", "1", "3"), result)
    }

    @Test
    fun `printListAt returns a null currentLabel when nothing before or at the position qualifies`() {
        val result = LivePageProbe.printListAt(pageList, labels, sectionIndex = 0, before = emptySet())
        assertEquals(ReaderProgress.PrintList(null, "1", "3"), result)
    }

    @Test
    fun `printListAt returns null for an empty entry list`() {
        assertNull(LivePageProbe.printListAt(emptyList(), emptyList(), sectionIndex = 0, before = emptySet()))
    }

    @Test
    fun `printListAt drops a non-numeric label`() {
        val mixedPageList = listOf(1 to "a", 2 to "b")
        val mixedLabels = listOf("i", "4")
        val result = LivePageProbe.printListAt(mixedPageList, mixedLabels, sectionIndex = 5, before = emptySet())
        assertEquals(ReaderProgress.PrintList("4", "4", "4"), result)
    }

    // Label parsing mirrors JS `parseInt(text)` (epub.js `pagelist.js`): leading
    // whitespace is skipped and parsing stops at the first non-digit, rather than
    // requiring the whole label to be a clean integer.

    @Test
    fun `printListAt parses a label with leading whitespace like parseInt`() {
        val result = LivePageProbe.printListAt(listOf(1 to "a"), listOf(" 12\n"), sectionIndex = 5, before = emptySet())
        assertEquals(ReaderProgress.PrintList("12", "12", "12"), result)
    }

    @Test
    fun `printListAt parses a label with trailing garbage like parseInt`() {
        val result = LivePageProbe.printListAt(listOf(1 to "a"), listOf("12a"), sectionIndex = 5, before = emptySet())
        assertEquals(ReaderProgress.PrintList("12", "12", "12"), result)
    }

    @Test
    fun `printListAt parses a label with leading spaces like parseInt`() {
        val result = LivePageProbe.printListAt(listOf(1 to "a"), listOf("  7"), sectionIndex = 5, before = emptySet())
        assertEquals(ReaderProgress.PrintList("7", "7", "7"), result)
    }

    @Test
    fun `printListAt drops a label with no leading integer`() {
        assertNull(LivePageProbe.printListAt(listOf(1 to "a"), listOf("xii"), sectionIndex = 5, before = emptySet()))
        assertNull(LivePageProbe.printListAt(listOf(1 to "a"), listOf(""), sectionIndex = 5, before = emptySet()))
        assertNull(LivePageProbe.printListAt(listOf(1 to "a"), listOf("-"), sectionIndex = 5, before = emptySet()))
    }
}
