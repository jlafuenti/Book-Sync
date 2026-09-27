package com.booksync.ui.reader

import com.booksync.ui.reader.PageCounterRequests.Route
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
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
    fun `other hosts are blocked, other schemes left to the WebView`() {
        // Remote resources are blocked since issue #736; plain http is never ours.
        assertEquals(Route.Blocked, PageCounterRequests.route("https://example.com/a.css"))
        assertEquals(Route.Blocked, PageCounterRequests.route("http://readium_package/a.xhtml"))
        assertEquals(Route.None, PageCounterRequests.route("about:blank"))
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
    fun `a URL that URI rejects is not ours`() {
        assertEquals(Route.None, PageCounterRequests.route("https://readium_package/Text/My Chapter.xhtml"))
        assertEquals(Route.None, PageCounterRequests.route("https://readium_package/a%zz.xhtml"))
        assertEquals(Route.None, PageCounterRequests.route("https://readium_assets/readium/a b.css"))
    }

    @Test
    fun `the shell URL with a load sequence and a fragment still routes to Shell`() {
        assertEquals(Route.Shell, PageCounterRequests.route("${PageCounterRequests.SHELL_URL}?n=42#top"))
    }

    @Test
    fun `a URL with no path after the host is not routed`() {
        assertEquals(Route.None, PageCounterRequests.route("https://readium_package"))
        assertEquals(Route.None, PageCounterRequests.route("https://readium_assets?x=1"))
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

    @Test
    fun `countScript passes the options as a JSON object`() {
        val js = PageCounterRequests.countScript(
            listOf("a.xhtml"),
            PageCounterRequests.CounterOptions(pubLang = "e\"n", dir = "rtl"),
        )
        assertTrue(js.contains(""""pubLang":"e\"n""""))
        assertTrue(js.contains(""""dir":"rtl""""))
    }

    // ---- shellUrl ----

    @Test
    fun `each load gets its own shell URL, and every one routes to Shell`() {
        val a = PageCounterRequests.shellUrl(1)
        val b = PageCounterRequests.shellUrl(2)
        assertNotEquals(a, b)
        assertEquals(Route.Shell, PageCounterRequests.route(a))
        assertEquals(Route.Shell, PageCounterRequests.route(b))
        assertTrue(a.startsWith(PageCounterRequests.SHELL_URL + "?"))
    }

    // ---- sanitizeHead ----

    @Test
    fun `sanitizeHead keeps link, style and meta, and drops everything else`() {
        val out = PageCounterRequests.sanitizeHead(
            listOf(
                """<link rel="stylesheet" href="https://readium_assets/readium/readium-css/ReadiumCSS-before.css">""",
                "<style>a{color:red}</style>",
                """<meta name="viewport" content="width=device-width">""",
                "<script>alert(1)</script>",
                "<title>T</title>",
                """<base href="https://example.com/">""",
                "<div>x</div>",
                """<noscript><style>b{}</style></noscript>""",
            ),
        )
        assertEquals(
            listOf(
                """<link rel="stylesheet" href="https://readium_assets/readium/readium-css/ReadiumCSS-before.css">""",
                "<style>a{color:red}</style>",
                """<meta name="viewport" content="width=device-width">""",
            ),
            out,
        )
    }

    @Test
    fun `sanitizeHead strips event handler attributes`() {
        val out = PageCounterRequests.sanitizeHead(
            listOf("""<link rel="stylesheet" href="a.css" onload="x()" OnError="y()">""", """<style onload="z()">p{}</style>"""),
        )
        assertEquals(listOf("""<link rel="stylesheet" href="a.css">""", "<style>p{}</style>"), out)
    }

    @Test
    fun `sanitizeHead drops http-equiv metas, which can navigate`() {
        assertEquals(
            emptyList<String>(),
            PageCounterRequests.sanitizeHead(listOf("""<meta http-equiv="refresh" content="0;url=https://example.com/">""")),
        )
    }

    @Test
    fun `sanitizeHead cannot be broken out of by a node that closes the head`() {
        val out = PageCounterRequests.sanitizeHead(listOf("""</head><body><img src=x onerror="alert(1)"><style>q{}</style>"""))
        assertTrue(out.none { it.contains("onerror") || it.contains("<img") })
    }

    @Test
    fun `sanitizeHead leaves style text alone`() {
        val css = """@font-face { font-family: "A&B"; src: url("https://readium_assets/f.woff2"); } p > a {}"""
        assertEquals(listOf("<style>$css</style>"), PageCounterRequests.sanitizeHead(listOf("<style>$css</style>")))
    }

    // ---- prepareHead ----

    private val before = """<link rel="stylesheet" type="text/css" href="https://readium_assets/readium/readium-css/ReadiumCSS-before.css">"""
    private val audio = "<style>audio[controls] { width: revert; height: revert; }</style>"
    private val overflow = "<style>:root { overflow: visible !important; }</style>"
    private val default = """<link rel="stylesheet" type="text/css" href="https://readium_assets/readium/readium-css/ReadiumCSS-default.css">"""
    private val after = """<link rel="stylesheet" type="text/css" href="https://readium_assets/readium/readium-css/ReadiumCSS-after.css">"""
    private val viewport = """<meta name="viewport" content="width=device-width">"""
    private val defaultCssHref = "https://readium_assets/readium/readium-css/ReadiumCSS-default.css"

    @Test
    fun `prepareHead excludes a captured default css, which Readium adds per resource`() {
        val prepared = PageCounterRequests.prepareHead(listOf(before, audio, overflow, default, after, viewport), liveDir = "ltr")
        assertTrue(prepared.nodes.none { it.contains("ReadiumCSS-default.css") && !it.contains(PageCounterRequests.DEFAULT_CSS_MARKER) })
        assertEquals(1, prepared.nodes.count { it.contains("ReadiumCSS-default.css") })
    }

    @Test
    fun `prepareHead derives default css from before css and places it right after before and its styles`() {
        val prepared = PageCounterRequests.prepareHead(listOf(before, audio, overflow, after, viewport), liveDir = "ltr")
        assertEquals(defaultCssHref, prepared.defaultCssHref)
        val nodes = prepared.nodes
        assertEquals(6, nodes.size)
        assertEquals(listOf(before, audio, overflow), nodes.subList(0, 3))
        assertTrue(nodes[3].contains(defaultCssHref))
        assertTrue(nodes[3].contains(PageCounterRequests.DEFAULT_CSS_MARKER))
        assertEquals(listOf(after, viewport), nodes.subList(4, 6))
    }

    @Test
    fun `prepareHead follows the stylesheet folder, as for RTL`() {
        val rtlBefore = before.replace("readium-css/", "readium-css/rtl/")
        val prepared = PageCounterRequests.prepareHead(listOf(rtlBefore, after), liveDir = "rtl")
        assertEquals("https://readium_assets/readium/readium-css/rtl/ReadiumCSS-default.css", prepared.defaultCssHref)
    }

    @Test
    fun `prepareHead without a before css adds no default css`() {
        val prepared = PageCounterRequests.prepareHead(listOf(audio, viewport), liveDir = null)
        assertNull(prepared.defaultCssHref)
        assertEquals(listOf(audio, viewport), prepared.nodes)
    }

    @Test
    fun `prepareHead sanitizes the captured nodes`() {
        val prepared = PageCounterRequests.prepareHead(listOf(before, "<script>x()</script>"), liveDir = "ltr")
        assertTrue(prepared.nodes.none { it.contains("<script") })
    }

    // ---- forced dir ----

    @Test
    fun `dir is forced to the live value for the default, RTL and CJK horizontal stylesheets`() {
        assertEquals("ltr", PageCounterRequests.prepareHead(listOf(before), liveDir = "ltr").forcedDir)
        val rtl = before.replace("readium-css/", "readium-css/rtl/")
        assertEquals("rtl", PageCounterRequests.prepareHead(listOf(rtl), liveDir = "rtl").forcedDir)
        val cjkH = before.replace("readium-css/", "readium-css/cjk-horizontal/")
        assertEquals("ltr", PageCounterRequests.prepareHead(listOf(cjkH), liveDir = "ltr").forcedDir)
    }

    @Test
    fun `dir falls back to the stylesheet folder when the live value is missing or odd`() {
        assertEquals("ltr", PageCounterRequests.prepareHead(listOf(before), liveDir = null).forcedDir)
        val rtl = before.replace("readium-css/", "readium-css/rtl/")
        assertEquals("rtl", PageCounterRequests.prepareHead(listOf(rtl), liveDir = "auto").forcedDir)
    }

    @Test
    fun `dir is not forced for CJK vertical, or when there is no before css`() {
        val cjkV = before.replace("readium-css/", "readium-css/cjk-vertical/")
        assertNull(PageCounterRequests.prepareHead(listOf(cjkV), liveDir = "rtl").forcedDir)
        assertNull(PageCounterRequests.prepareHead(listOf(audio), liveDir = "ltr").forcedDir)
    }

    // ---- resolveLang (mirrored by page-counter.js) ----

    private fun lang(lang: String? = null, xmlLang: String? = null) = PageCounterRequests.LangAttrs(lang, xmlLang)

    @Test
    fun `resolveLang keeps the book's own html language`() {
        assertEquals(
            PageCounterRequests.ResolvedLang("fr", null),
            PageCounterRequests.resolveLang(false, lang(lang = "fr"), lang(), pubLang = "en"),
        )
        assertEquals(
            PageCounterRequests.ResolvedLang("de", null),
            PageCounterRequests.resolveLang(true, lang(lang = "fr", xmlLang = "de"), lang(), pubLang = "en"),
        )
    }

    @Test
    fun `resolveLang in XHTML lifts the body language to html`() {
        assertEquals(
            PageCounterRequests.ResolvedLang("fr", "fr"),
            PageCounterRequests.resolveLang(true, lang(), lang(xmlLang = "fr"), pubLang = "en"),
        )
        assertEquals(
            PageCounterRequests.ResolvedLang("es", "es"),
            PageCounterRequests.resolveLang(true, lang(), lang(lang = "es"), pubLang = "en"),
        )
    }

    @Test
    fun `resolveLang in XHTML falls back to the publication language on html and body`() {
        assertEquals(
            PageCounterRequests.ResolvedLang("en", "en"),
            PageCounterRequests.resolveLang(true, lang(), lang(), pubLang = "en"),
        )
    }

    @Test
    fun `resolveLang uses the publication language when the body language is empty`() {
        assertEquals(
            PageCounterRequests.ResolvedLang("en", ""),
            PageCounterRequests.resolveLang(true, lang(), lang(lang = ""), pubLang = "en"),
        )
    }

    @Test
    fun `resolveLang without a publication language changes nothing`() {
        assertEquals(
            PageCounterRequests.ResolvedLang(null, null),
            PageCounterRequests.resolveLang(true, lang(), lang(), pubLang = null),
        )
    }

    @Test
    fun `resolveLang in HTML ignores xml lang, the book's and Readium's alike`() {
        // HTML documents give a no-namespace xml:lang no effect, so Readium's
        // injected xml:lang does not change the language of an .html resource.
        assertEquals(
            PageCounterRequests.ResolvedLang(null, null),
            PageCounterRequests.resolveLang(false, lang(), lang(), pubLang = "en"),
        )
        assertEquals(
            PageCounterRequests.ResolvedLang(null, "es"),
            PageCounterRequests.resolveLang(false, lang(xmlLang = "fr"), lang(lang = "es"), pubLang = "en"),
        )
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
    // ---- remote resources (issue #736) ----
    // The counter lays out every chapter when a book opens; letting it fetch a
    // remote image or font would contact that host for chapters the reader has
    // not opened. Anything not served by the book or the app is blocked.

    @Test
    fun `a remote https or http resource is blocked`() {
        assertEquals(Route.Blocked, PageCounterRequests.route("https://images.example.com/cover.jpg"))
        assertEquals(Route.Blocked, PageCounterRequests.route("http://fonts.example.com/f.woff2"))
    }

    @Test
    fun `other schemes are still left to the WebView`() {
        assertEquals(Route.None, PageCounterRequests.route("about:blank"))
    }
}
