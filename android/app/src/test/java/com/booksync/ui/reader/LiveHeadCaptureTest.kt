package com.booksync.ui.reader

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Issue #730, task 11. [LiveHeadCapture] copies the head nodes Readium injected
 * into the live page, so the hidden page counter can lay out exactly like the
 * reader. The diff is pure Kotlin ([LiveHeadCapture.injectedNodes]) and the
 * JS mirrors it.
 */
class LiveHeadCaptureTest {

    // ---- injectedNodes ----

    @Test
    fun `injectedNodes keeps live nodes absent from the raw head, in live order`() {
        assertEquals(
            listOf("R1", "R2"),
            LiveHeadCapture.injectedNodes(live = listOf("A", "R1", "B", "R2"), raw = listOf("A", "B")),
        )
    }

    @Test
    fun `injectedNodes with an empty raw head keeps everything`() {
        assertEquals(listOf("A", "B"), LiveHeadCapture.injectedNodes(listOf("A", "B"), emptyList()))
    }

    @Test
    fun `injectedNodes ignores raw nodes the live head no longer has`() {
        assertEquals(listOf("R1"), LiveHeadCapture.injectedNodes(listOf("R1", "A"), listOf("A", "GONE")))
    }

    @Test
    fun `each raw node cancels one live copy, so an injected duplicate of a book node survives`() {
        assertEquals(
            listOf("S"),
            LiveHeadCapture.injectedNodes(live = listOf("S", "A", "S"), raw = listOf("A", "S")),
        )
    }

    // ---- script ----

    @Test
    fun `script embeds the raw head as a JSON string literal`() {
        val raw = """<head><link rel="stylesheet" href="a&b.css"/><title>It's "x"</title></head>"""
        val js = LiveHeadCapture.script(raw)
        // Quotes are JSON-escaped, and "</" becomes "<\/" (see the next test).
        assertTrue(js.contains("""<title>It's \"x\"<\/title>"""))
        assertFalse(js.contains("""<title>It's "x""""))
        assertTrue(js.contains("DOMParser"))
    }

    @Test
    fun `script excludes script nodes and reads the html style`() {
        val js = LiveHeadCapture.script("")
        assertTrue(js.contains("'script'"))
        assertTrue(js.contains("getAttribute('style')"))
        assertTrue(js.contains("JSON.stringify"))
    }

    @Test
    fun `script survives a closing script tag in the raw head`() {
        val js = LiveHeadCapture.script("<script>x()</script>")
        // A literal "</script>" inside the evaluated source is harmless for
        // evaluateJavascript, but "<\/" keeps it inert anywhere else too.
        assertFalse(js.contains("</script>"))
    }

    // ---- parse ----

    @Test
    fun `parse reads the double-encoded result evaluateJavascript hands back`() {
        val inner = """{"style":"--USER__view: readium-paged-on !important;","head":["<style>a{}</style>"]}"""
        val doubled = kotlinx.serialization.json.JsonPrimitive(inner).toString()
        assertEquals(
            LiveHeadCapture.Captured(
                style = "--USER__view: readium-paged-on !important;",
                head = listOf("<style>a{}</style>"),
            ),
            LiveHeadCapture.parse(doubled),
        )
    }

    @Test
    fun `parse accepts a plain object and a missing style`() {
        assertEquals(
            LiveHeadCapture.Captured(style = "", head = emptyList()),
            LiveHeadCapture.parse("""{"head":[]}"""),
        )
    }

    @Test
    fun `parse returns null for null, empty and garbage`() {
        assertNull(LiveHeadCapture.parse(null))
        assertNull(LiveHeadCapture.parse("null"))
        assertNull(LiveHeadCapture.parse(""))
        assertNull(LiveHeadCapture.parse("{not json"))
        assertNull(LiveHeadCapture.parse("""{"style":"x"}"""))
    }
}
