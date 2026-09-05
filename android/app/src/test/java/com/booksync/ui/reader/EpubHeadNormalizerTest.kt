package com.booksync.ui.reader

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Issue #373. Readium 3.1.2 injects its CSS and JS by searching the resource
 * for a literal `</head>`; a well-formed `<head/>` — exactly what Calibre
 * writes for the SVG cover title page it generates — has no such tag, so the
 * injector throws "No </head> closing tag found in this resource" and the
 * WebView gets `net::ERR_FAILED` for that spine item.
 *
 * These tests pin the two halves of the rewrite: it must fix the self-closing
 * head in every shape it appears in, and it must not touch a single other byte
 * of the document — the transformer runs on every XHTML resource of every book
 * the reader opens.
 */
class EpubHeadNormalizerTest {

    // ---------- rewrites ----------

    @Test
    fun `bare self-closing head becomes an empty head pair`() {
        assertEquals(
            "<html><head></head><body>x</body></html>",
            normalizeEpubHead("<html><head/><body>x</body></html>"),
        )
    }

    @Test
    fun `space before the slash is accepted`() {
        assertEquals(
            "<html><head></head><body>x</body></html>",
            normalizeEpubHead("<html><head /><body>x</body></html>"),
        )
    }

    @Test
    fun `arbitrary whitespace around the slash is accepted`() {
        assertEquals(
            "<head></head>",
            normalizeEpubHead("<head\n\t /\n>"),
        )
    }

    @Test
    fun `attributes are preserved on the rewritten open tag`() {
        assertEquals(
            """<head profile="http://example.invalid/profile"></head>""",
            normalizeEpubHead("""<head profile="http://example.invalid/profile"/>"""),
        )
    }

    @Test
    fun `an attribute value containing a slash does not end the tag early`() {
        assertEquals(
            """<head xml:base="a/b/c/"></head>""",
            normalizeEpubHead("""<head xml:base="a/b/c/"/>"""),
        )
    }

    @Test
    fun `uppercase head is rewritten and its case is preserved`() {
        assertEquals("<HEAD></HEAD>", normalizeEpubHead("<HEAD/>"))
    }

    @Test
    fun `mixed case head is rewritten and its case is preserved`() {
        assertEquals("<Head></Head>", normalizeEpubHead("<Head />"))
    }

    @Test
    fun `the Calibre title page shape is fixed`() {
        val calibre = """
            <?xml version='1.0' encoding='utf-8'?>
            <html xmlns="http://www.w3.org/1999/xhtml">
              <head/>
              <body><div><svg><image xlink:href="../Images/cover.jpeg"/></svg></div></body>
            </html>
        """.trimIndent()
        val out = normalizeEpubHead(calibre)
        assertTrue("injector must be able to find a closing head", out.contains("</head>"))
        // The SVG's own self-closing <image/> must survive untouched.
        assertTrue(out.contains("""<image xlink:href="../Images/cover.jpeg"/>"""))
        assertEquals(calibre.replace("<head/>", "<head></head>"), out)
    }

    @Test
    fun `every self-closing head in a document is rewritten`() {
        assertEquals(
            "<head></head>x<head></head>",
            normalizeEpubHead("<head/>x<head />"),
        )
    }

    // ---------- leaves alone ----------

    @Test
    fun `an already normal head is returned unchanged and identical`() {
        val html = "<html><head><title>t</title></head><body>x</body></html>"
        assertSame(html, normalizeEpubHead(html))
    }

    @Test
    fun `an empty head pair is returned unchanged`() {
        val html = "<html><head></head><body>x</body></html>"
        assertSame(html, normalizeEpubHead(html))
    }

    @Test
    fun `a document with no head at all is returned unchanged`() {
        val html = "<html><body>x</body></html>"
        assertSame(html, normalizeEpubHead(html))
    }

    @Test
    fun `body content is never touched`() {
        val html = "<html><head></head><body><p>a &lt;head/&gt; in text</p><br/></body></html>"
        assertSame(html, normalizeEpubHead(html))
    }

    @Test
    fun `a self-closing header element is not touched`() {
        val html = "<html><head></head><body><header/></body></html>"
        assertSame(html, normalizeEpubHead(html))
    }

    @Test
    fun `a self-closing header element with attributes is not touched`() {
        val html = """<body><header class="x"/><headerfoo/></body>"""
        assertSame(html, normalizeEpubHead(html))
    }

    @Test
    fun `a namespaced self-closing element ending in head is not touched`() {
        val html = "<body><x:head/></body>"
        assertSame(html, normalizeEpubHead(html))
    }

    @Test
    fun `an empty document is returned unchanged`() {
        assertSame("", normalizeEpubHead(""))
    }

    @Test
    fun `a normal head elsewhere does not stop a later self-closing head`() {
        assertEquals(
            "<head></head>...<head></head>",
            normalizeEpubHead("<head></head>...<head/>"),
        )
    }

    // ---------- byte-level wrapper ----------

    @Test
    fun `bytes needing no change are returned as the very same array`() {
        val bytes = "<html><head></head><body>x</body></html>".toByteArray()
        assertSame(bytes, normalizeEpubHeadBytes(bytes))
    }

    @Test
    fun `bytes with a self-closing head come back rewritten`() {
        val out = normalizeEpubHeadBytes("<html><head/><body>x</body></html>".toByteArray())
        assertEquals("<html><head></head><body>x</body></html>", String(out, Charsets.UTF_8))
    }

    @Test
    fun `non-ASCII bytes survive the round trip byte for byte`() {
        // The rewrite decodes as Latin-1 so that any byte sequence round-trips
        // unchanged; a UTF-8 document must not be re-encoded or mangled.
        val html = "<html><head/><body>é — 日本語 📖</body></html>"
        val expected = "<html><head></head><body>é — 日本語 📖</body></html>"
        assertArrayEquals(
            expected.toByteArray(Charsets.UTF_8),
            normalizeEpubHeadBytes(html.toByteArray(Charsets.UTF_8)),
        )
    }

    @Test
    fun `arbitrary binary bytes are returned untouched`() {
        val bytes = ByteArray(256) { (it - 128).toByte() }
        assertSame(bytes, normalizeEpubHeadBytes(bytes))
    }

    // ---------- which resources get transformed ----------

    @Test
    fun `html extensions are transformed`() {
        assertTrue(isHtmlLikeExtension("xhtml"))
        assertTrue(isHtmlLikeExtension("html"))
        assertTrue(isHtmlLikeExtension("htm"))
        assertTrue(isHtmlLikeExtension("XHTML"))
    }

    @Test
    fun `other extensions are not transformed`() {
        assertFalse(isHtmlLikeExtension("css"))
        assertFalse(isHtmlLikeExtension("jpeg"))
        assertFalse(isHtmlLikeExtension("ncx"))
        assertFalse(isHtmlLikeExtension("opf"))
        assertFalse(isHtmlLikeExtension(""))
        assertFalse(isHtmlLikeExtension(null))
    }
}
