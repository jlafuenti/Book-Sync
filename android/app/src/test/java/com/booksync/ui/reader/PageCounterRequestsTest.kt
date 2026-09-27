package com.booksync.ui.reader

import com.booksync.ui.reader.PageCounterRequests.Route
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Issue #730, task 11. The hidden page-counter WebView routes every request
 * through [PageCounterRequests.route], serves its shell document from
 * [PageCounterRequests.shellHtml], and parses the JS bridge's result with
 * [PageCounterRequests.parseCounts]. All three are pure, so they are tested
 * here; the WebView itself is verified on the emulator.
 */
class PageCounterRequestsTest {

    // ---- route ----

    @Test
    fun `package host maps to a Package route with the path relative to the root`() {
        assertEquals(
            Route.Package("OEBPS/chapter1.xhtml"),
            PageCounterRequests.route("https://readium_package/OEBPS/chapter1.xhtml"),
        )
    }

    @Test
    fun `assets host maps to an Asset route`() {
        assertEquals(
            Route.Asset("readium/readium-css/ReadiumCSS-before.css"),
            PageCounterRequests.route("https://readium_assets/readium/readium-css/ReadiumCSS-before.css"),
        )
    }

    @Test
    fun `the counter's own script is an asset`() {
        assertEquals(
            Route.Asset("tandem/page-counter.js"),
            PageCounterRequests.route("https://readium_assets/tandem/page-counter.js"),
        )
    }

    @Test
    fun `the shell URL maps to Shell, not Package`() {
        assertEquals(Route.Shell, PageCounterRequests.route(PageCounterRequests.SHELL_URL))
        assertEquals(Route.Shell, PageCounterRequests.route("https://readium_package/__tandem_page_counter__.html"))
    }

    @Test
    fun `other hosts and schemes map to None`() {
        assertEquals(Route.None, PageCounterRequests.route("https://example.com/a.css"))
        assertEquals(Route.None, PageCounterRequests.route("about:blank"))
        assertEquals(Route.None, PageCounterRequests.route("http://readium_package/a.xhtml"))
        assertEquals(Route.None, PageCounterRequests.route("https://readium_package/"))
        assertEquals(Route.None, PageCounterRequests.route(""))
    }

    @Test
    fun `query string and fragment are dropped`() {
        assertEquals(
            Route.Package("text/part0001.html"),
            PageCounterRequests.route("https://readium_package/text/part0001.html?v=2#frag"),
        )
        assertEquals(
            Route.Asset("readium/readium-css/ReadiumCSS-after.css"),
            PageCounterRequests.route("https://readium_assets/readium/readium-css/ReadiumCSS-after.css?x=1"),
        )
        assertEquals(Route.Shell, PageCounterRequests.route("${PageCounterRequests.SHELL_URL}?n=3"))
    }

    @Test
    fun `package path stays percent-encoded, because Readium's Url parses an encoded URL`() {
        assertEquals(
            Route.Package("Text/My%20Chapter.xhtml"),
            PageCounterRequests.route("https://readium_package/Text/My%20Chapter.xhtml"),
        )
    }

    @Test
    fun `asset path is decoded, because AssetManager opens a literal file name`() {
        assertEquals(
            Route.Asset("readium/fonts/Some Font.otf"),
            PageCounterRequests.route("https://readium_assets/readium/fonts/Some%20Font.otf"),
        )
    }

    @Test
    fun `host match ignores case`() {
        assertEquals(
            Route.Package("a.xhtml"),
            PageCounterRequests.route("https://READIUM_PACKAGE/a.xhtml"),
        )
    }

    @Test
    fun `assets outside the readium and tandem folders, or escaping them, are not served`() {
        assertEquals(Route.None, PageCounterRequests.route("https://readium_assets/other/secret.json"))
        assertEquals(Route.None, PageCounterRequests.route("https://readium_assets/readium/../other/secret.json"))
        assertEquals(Route.None, PageCounterRequests.route("https://readium_assets/readium/%2E%2E/other/x"))
        assertEquals(Route.None, PageCounterRequests.route("https://readium_assets/readium//x.css"))
    }

    @Test
    fun `a malformed percent escape in an asset path is not served`() {
        assertEquals(Route.None, PageCounterRequests.route("https://readium_assets/readium/a%2"))
        assertEquals(Route.None, PageCounterRequests.route("https://readium_assets/readium/a%zz.css"))
    }

    // ---- mediaTypeFor ----

    @Test
    fun `mediaTypeFor falls back by extension`() {
        assertEquals("application/xhtml+xml", PageCounterRequests.mediaTypeFor("a/b.xhtml"))
        assertEquals("text/html", PageCounterRequests.mediaTypeFor("a/b.html"))
        assertEquals("text/html", PageCounterRequests.mediaTypeFor("a/b.HTM"))
        assertEquals("text/css", PageCounterRequests.mediaTypeFor("x.css"))
        assertEquals("text/javascript", PageCounterRequests.mediaTypeFor("tandem/page-counter.js"))
        assertEquals("font/woff2", PageCounterRequests.mediaTypeFor("f.woff2"))
        assertEquals("image/jpeg", PageCounterRequests.mediaTypeFor("i.jpg"))
        assertEquals("application/octet-stream", PageCounterRequests.mediaTypeFor("noext"))
    }

    // ---- shellHtml ----

    @Test
    fun `shellHtml escapes quote, less-than and ampersand in the style attribute`() {
        val html = PageCounterRequests.shellHtml(
            style = """--USER__fontFamily: "A&B" <x> !important;""",
            headNodes = emptyList(),
        )
        assertTrue(html.contains("""style="--USER__fontFamily: &quot;A&amp;B&quot; &lt;x&gt; !important;""""))
        assertFalse(html.contains("\"A&B\""))
    }

    @Test
    fun `shellHtml includes the head nodes in order, and loads the counter script last`() {
        val a = """<link rel="stylesheet" href="https://readium_assets/readium/readium-css/ReadiumCSS-before.css">"""
        val b = "<style>audio[controls] { width: revert; }</style>"
        val html = PageCounterRequests.shellHtml("--USER__view: readium-paged-on !important;", listOf(a, b))
        val ia = html.indexOf(a)
        val ib = html.indexOf(b)
        val iScript = html.indexOf("""<script src="${PageCounterRequests.COUNTER_SCRIPT_URL}"></script>""")
        assertTrue(ia > 0)
        assertTrue(ib > ia)
        assertTrue(iScript > ib)
        assertTrue(html.startsWith("<!DOCTYPE html>"))
        assertTrue(html.contains("<body></body>"))
    }

    @Test
    fun `shellHtml carries Readium's default viewport before the captured nodes`() {
        val html = PageCounterRequests.shellHtml("", listOf("<style>x{}</style>"))
        val iViewport = html.indexOf("""<meta name="viewport"""")
        assertTrue(iViewport > 0)
        assertTrue(iViewport < html.indexOf("<style>x{}</style>"))
    }

    // ---- countScript ----

    @Test
    fun `countScript passes the hrefs as a JSON array literal`() {
        val js = PageCounterRequests.countScript(listOf("a.xhtml", """b"c\d.html"""))
        assertTrue(js.contains("""["a.xhtml","b\"c\\d.html"]"""))
        assertTrue(js.contains("tandemCountPages"))
        assertTrue(js.contains("TandemPageCounter.onError"))
    }

    // ---- parseCounts ----

    @Test
    fun `parseCounts reads counts and chars`() {
        assertEquals(
            Counts(listOf(1, 12, 3), listOf(10, 4000, 900)),
            PageCounterRequests.parseCounts("""{"counts":[1,12,3],"chars":[10,4000,900]}""", expected = 3),
        )
    }

    @Test
    fun `parseCounts rejects garbage, mismatched lengths and the wrong size`() {
        assertNull(PageCounterRequests.parseCounts("nope", expected = 1))
        assertNull(PageCounterRequests.parseCounts("""{"counts":[1,2],"chars":[1]}""", expected = 2))
        assertNull(PageCounterRequests.parseCounts("""{"counts":[1],"chars":[1]}""", expected = 2))
        assertNull(PageCounterRequests.parseCounts("""{"counts":[-1],"chars":[1]}""", expected = 1))
        assertNull(PageCounterRequests.parseCounts("""{"chars":[1]}""", expected = 1))
    }
}
