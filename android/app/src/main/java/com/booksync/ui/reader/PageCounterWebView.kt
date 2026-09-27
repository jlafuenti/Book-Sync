package com.booksync.ui.reader

import android.annotation.SuppressLint
import android.content.Context
import android.util.Log
import android.webkit.JavascriptInterface
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.selects.select
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonObject
import org.jsoup.Jsoup
import org.readium.r2.shared.publication.Publication
import org.readium.r2.shared.util.Url
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.IOException

/**
 * The pure half of [PageCounterWebView] (issue #730, task 11): how its requests
 * are routed, the shell document it counts in, the script that starts a count,
 * and how the result is parsed. Tested in `PageCounterRequestsTest`.
 *
 * The hidden WebView uses Readium's own origins - `https://readium_package/`
 * for the book, `https://readium_assets/` for ReadiumCSS and its fonts - so the
 * head nodes copied from the live reader ([LiveHeadCapture]) resolve unchanged.
 * The shell itself lives on the package origin, which keeps every chapter
 * `fetch` same-origin.
 */
object PageCounterRequests {

    const val PACKAGE_HOST = "readium_package"
    const val ASSETS_HOST = "readium_assets"
    private const val SHELL_PATH = "__tandem_page_counter__.html"
    const val SHELL_URL = "https://$PACKAGE_HOST/$SHELL_PATH"
    const val COUNTER_SCRIPT_URL = "https://$ASSETS_HOST/tandem/page-counter.js"

    /**
     * Readium appends exactly this viewport `<meta>` on DOMContentLoaded
     * (`readium-reflowable.js`). The capture normally carries it; the shell
     * repeats it first so the layout viewport is device-width even if it does
     * not. A later captured copy simply wins.
     */
    private const val READIUM_VIEWPORT =
        """<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, shrink-to-fit=no">"""

    /** App assets the WebView may read: ReadiumCSS and fonts, and our own script. */
    private val ASSET_PREFIXES = listOf("readium/", "tandem/")

    sealed interface Route {
        /** A publication resource; [path] is still percent-encoded, as Readium's `Url(String)` expects. */
        data class Package(val path: String) : Route

        /** An APK asset; [path] is decoded, as `AssetManager.open` expects. */
        data class Asset(val path: String) : Route

        /** The counter's shell document. */
        data object Shell : Route

        /** Not ours: let the WebView handle it, as Readium's own server does. */
        data object None : Route
    }

    private val URL_PATTERN = Regex("""^https://([^/?#]+)/([^?#]*)""")

    fun route(url: String): Route {
        val match = URL_PATTERN.find(url) ?: return Route.None
        val host = match.groupValues[1].lowercase()
        val path = match.groupValues[2]
        if (path.isEmpty()) return Route.None
        return when (host) {
            PACKAGE_HOST -> if (path == SHELL_PATH) Route.Shell else Route.Package(path)
            ASSETS_HOST -> {
                val decoded = percentDecode(path) ?: return Route.None
                val segments = decoded.split('/')
                when {
                    segments.any { it == ".." || it == "." || it.isEmpty() } -> Route.None
                    ASSET_PREFIXES.none { decoded.startsWith(it) } -> Route.None
                    else -> Route.Asset(decoded)
                }
            }
            else -> Route.None
        }
    }

    /** The media type by extension, for assets and for a package path the publication has no link for. */
    fun mediaTypeFor(path: String): String =
        when (path.substringAfterLast('/').substringAfterLast('.', "").lowercase()) {
            "xhtml", "xht", "xml" -> "application/xhtml+xml"
            "html", "htm" -> "text/html"
            "css" -> "text/css"
            "js" -> "text/javascript"
            "svg" -> "image/svg+xml"
            "jpg", "jpeg" -> "image/jpeg"
            "png" -> "image/png"
            "gif" -> "image/gif"
            "webp" -> "image/webp"
            "woff" -> "font/woff"
            "woff2" -> "font/woff2"
            "ttf" -> "font/ttf"
            "otf" -> "font/otf"
            else -> "application/octet-stream"
        }

    /**
     * The shell: Readium's `<html style>` (HTML-escaped) and the injected head
     * nodes captured from the live page, then `page-counter.js`, and an empty
     * body the script fills one resource at a time.
     */
    fun shellHtml(style: String, headNodes: List<String>): String = buildString {
        append("<!DOCTYPE html>\n")
        append("<html style=\"").append(escapeAttribute(style)).append("\"><head>\n")
        append("<meta charset=\"utf-8\">\n")
        append(READIUM_VIEWPORT).append('\n')
        headNodes.forEach { append(it).append('\n') }
        append("<script src=\"").append(COUNTER_SCRIPT_URL).append("\"></script>\n")
        append("</head><body></body></html>\n")
    }

    /** A shell URL unique to one load, so a late event from an aborted load cannot be mistaken for it. */
    fun shellUrl(seq: Long): String = "$SHELL_URL?n=$seq"

    /**
     * Per-count options for `tandemCountPages`: the publication language
     * Readium falls back to ([resolveLang]) and the dir it forces on `<html>`
     * and `<body>` ([PreparedHead.forcedDir]).
     */
    @Serializable
    data class CounterOptions(val pubLang: String? = null, val dir: String? = null)

    /**
     * Starts a count in the loaded shell. [hrefs] are reading-order hrefs
     * relative to the package root (Readium's `Link.url().toString()`); they and
     * [options] go in as JSON literals built by kotlinx.serialization. The
     * result comes back through the `TandemPageCounter` bridge, not as the
     * script's value.
     */
    fun countScript(hrefs: List<String>, options: CounterOptions = CounterOptions()): String {
        val hrefsLiteral = Json.encodeToString(ListSerializer(String.serializer()), hrefs).replace("</", "<\\/")
        val optionsLiteral = Json.encodeToString(CounterOptions.serializer(), options).replace("</", "<\\/")
        return """
            (function () {
              if (typeof window.tandemCountPages !== 'function') {
                TandemPageCounter.onError('page-counter.js did not load');
                return;
              }
              window.tandemCountPages($hrefsLiteral, $optionsLiteral);
            })();
        """.trimIndent()
    }

    /** Head elements the shell may carry. `<script>`, `<base>`, `<title>` and the rest are dropped. */
    private val HEAD_TAGS = setOf("link", "style", "meta")

    /**
     * Keeps only `link`, `style` and `meta` elements from captured head HTML,
     * drops `meta http-equiv` (a refresh can navigate), and strips every `on*`
     * attribute. Each string is parsed in a head of its own, so markup that
     * closes the head cannot smuggle anything into the shell. Pure: jsoup only.
     */
    fun sanitizeHead(nodes: List<String>): List<String> = nodes.flatMap { node ->
        val doc = Jsoup.parse("<html><head>$node</head><body></body></html>")
        doc.outputSettings().prettyPrint(false)
        doc.head().children().filter { el ->
            el.normalName() in HEAD_TAGS && !(el.normalName() == "meta" && el.hasAttr("http-equiv"))
        }.map { el ->
            el.attributes().asList().map { it.key }
                .filter { it.lowercase().startsWith("on") }
                .forEach { el.removeAttr(it) }
            el.outerHtml()
        }
    }

    /**
     * Marks the `ReadiumCSS-default.css` link [prepareHead] adds; `page-counter.js`
     * enables it per resource, exactly when Readium would inject it.
     */
    const val DEFAULT_CSS_MARKER = "data-tandem-default-css"

    private const val BEFORE_CSS = "ReadiumCSS-before.css"
    private const val DEFAULT_CSS = "ReadiumCSS-default.css"

    /** The shell head, ready to serve: see [prepareHead]. */
    data class PreparedHead(val nodes: List<String>, val defaultCssHref: String?, val forcedDir: String?)

    /**
     * Turns the captured head into the shell's head.
     *
     * - [sanitizeHead] first.
     * - `ReadiumCSS-default.css` is per resource: Readium's `ReadiumCss.injectStyles`
     *   adds it only when the resource's raw text has no styles (its `hasStyles`:
     *   no `<link`, no ` style=`, no `<style...>`, case-insensitive). A captured copy
     *   only says the page on screen was unstyled, so it is dropped, and a marked
     *   link is added where Readium puts it - right after `ReadiumCSS-before.css`
     *   and the `<style>` blocks that follow it - with its URL derived from the
     *   before link's, so the stylesheet folder (rtl, cjk-...) carries over.
     * - [PreparedHead.forcedDir]: Readium's `injectDir` strips the book's `dir` from
     *   `<html>` and `<body>` and sets its own on both, for every stylesheet set but
     *   CJK vertical (default and cjk-horizontal: ltr; rtl: rtl). The live `<html dir>`
     *   is that value; the folder is the fallback if the capture has none.
     */
    fun prepareHead(headNodes: List<String>, liveDir: String?): PreparedHead {
        val clean = sanitizeHead(headNodes).filterNot { it.contains(DEFAULT_CSS) }
        val beforeIndex = clean.indexOfFirst { it.startsWith("<link") && it.contains(BEFORE_CSS) }
        if (beforeIndex < 0) return PreparedHead(clean, defaultCssHref = null, forcedDir = null)

        val beforeHref = Jsoup.parse(clean[beforeIndex]).selectFirst("link")?.attr("href").orEmpty()
        val defaultHref = beforeHref.replace(BEFORE_CSS, DEFAULT_CSS)
        var insertAt = beforeIndex + 1
        while (insertAt < clean.size && clean[insertAt].startsWith("<style")) insertAt++
        val defaultLink =
            """<link rel="stylesheet" type="text/css" href="${escapeAttribute(defaultHref)}" $DEFAULT_CSS_MARKER="">"""
        val nodes = clean.subList(0, insertAt) + defaultLink + clean.subList(insertAt, clean.size)

        val folder = beforeHref.substringBeforeLast('/').substringAfterLast('/')
        val forcedDir = when {
            folder == "cjk-vertical" -> null
            liveDir == "ltr" || liveDir == "rtl" -> liveDir
            folder == "rtl" -> "rtl"
            else -> "ltr"
        }
        return PreparedHead(nodes, defaultHref, forcedDir)
    }

    /** A raw element's language attributes; `null` means absent, `""` present but empty. */
    data class LangAttrs(val lang: String? = null, val xmlLang: String? = null)

    /** The effective language of `<html>` and `<body>` in the live reader; `null` means none of its own. */
    data class ResolvedLang(val html: String?, val body: String?)

    /**
     * The language the live reader ends up with for one resource, which
     * `page-counter.js` mirrors (`resolveLang` there; hyphenation depends on it).
     *
     * Readium's `ReadiumCss.injectLang`: with a publication language [pubLang],
     * and neither `lang` nor `xml:lang` on `<html>`, it inserts `xml:lang` on
     * `<html>` - the body's (`xml:lang`, else `lang`, if non-empty, else
     * [pubLang]) when the body has either attribute, otherwise [pubLang] on both
     * `<html>` and `<body>`.
     *
     * What that means depends on how the resource is parsed. In XHTML (an XML
     * document) `xml:lang` wins over `lang`. In HTML a no-namespace `xml:lang`
     * has no effect on language, so for an `.html` resource Readium's insertion
     * changes nothing and only `lang` counts.
     */
    fun resolveLang(xhtml: Boolean, html: LangAttrs, body: LangAttrs, pubLang: String?): ResolvedLang {
        var h = html
        var b = body
        if (pubLang != null && h.lang == null && h.xmlLang == null) {
            if (b.lang != null || b.xmlLang != null) {
                h = h.copy(xmlLang = b.xmlLang?.ifEmpty { null } ?: b.lang?.ifEmpty { null } ?: pubLang)
            } else {
                h = h.copy(xmlLang = pubLang)
                b = b.copy(xmlLang = pubLang)
            }
        }
        fun effective(a: LangAttrs) = if (xhtml) a.xmlLang ?: a.lang else a.lang
        return ResolvedLang(effective(h), effective(b))
    }

    private val json = Json { ignoreUnknownKeys = true }

    /**
     * Parses `onDone`'s `{"counts": [...], "chars": [...]}`. Returns `null`
     * unless both are integer arrays of [expected] length with no negatives.
     */
    fun parseCounts(result: String, expected: Int): Counts? = try {
        val obj = json.parseToJsonElement(result).jsonObject
        val counts = ints(obj["counts"])
        val chars = ints(obj["chars"])
        if (counts == null || chars == null || counts.size != expected || chars.size != expected) {
            null
        } else if (counts.any { it < 0 } || chars.any { it < 0 }) {
            null
        } else {
            Counts(counts, chars)
        }
    } catch (e: Exception) {
        null
    }

    private fun ints(element: Any?): List<Int>? =
        (element as? JsonArray)?.map { (it as? JsonPrimitive)?.intOrNull ?: return null }

    private fun escapeAttribute(value: String): String = value
        .replace("&", "&amp;")
        .replace("\"", "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")

    /** Percent-decoding for a URL path: UTF-8, and `+` stays `+` (unlike `URLDecoder`). */
    private fun percentDecode(s: String): String? {
        val out = ByteArrayOutputStream()
        var i = 0
        while (i < s.length) {
            val c = s[i]
            if (c == '%') {
                if (i + 2 >= s.length) return null
                val byte = s.substring(i + 1, i + 3).toIntOrNull(16) ?: return null
                out.write(byte)
                i += 3
            } else {
                out.write(c.toString().toByteArray(Charsets.UTF_8))
                i++
            }
        }
        return out.toString("UTF-8")
    }
}

/**
 * Counts screen pages per reading-order resource off-screen (issue #730, task
 * 11), laid out exactly like Readium's live reader at its current settings.
 *
 * Why its own WebView: running the count inside Readium's page made the reader
 * show "This page could not be displayed", and Readium's CSS injection is
 * internal. So this view mirrors the live page instead: [count] takes the live
 * `<html style>` and injected head nodes ([LiveHeadCapture]), serves a shell
 * document built from them ([PageCounterRequests.shellHtml]), and runs
 * `assets/tandem/page-counter.js` in it. Every count starts from a fresh shell
 * load, at a URL of its own - reusing one document across a settings change
 * left stale layout behind in the spike.
 *
 * Known limitation: the shell is always in standards mode, so an `.html`
 * resource with no doctype, which the live reader renders in quirks mode, can
 * lay out slightly differently.
 *
 * Its settings mirror what Readium 3.4's `R2EpubPageFragment.onCreateView`
 * sets on its `R2WebView` (read from the navigator jar with `javap`): JS on,
 * scroll bars off, wide viewport and overview mode on, zoom supported with
 * built-in controls hidden, padding 0, and `textZoom` equal to Readium's own -
 * 100, since `EpubNavigatorFragment.Configuration.useReadiumCssFontSize`
 * defaults to true and the app does not change it, so Readium never moves it.
 * Leaving `textZoom` at WebView's default (the system font scale) would skew
 * every count on a device with a non-default font size.
 *
 * It is `INVISIBLE`, not `GONE`: it must still be laid out, at the live
 * reader's WebView size, for the columns to match. Placing it is the caller's
 * job (Task 12).
 */
@SuppressLint("SetJavaScriptEnabled", "ViewConstructor")
class PageCounterWebView(
    context: Context,
    private val publication: Publication,
) : WebView(context) {

    private class Shell(val url: String, val html: String)

    @Volatile private var shell: Shell? = null
    @Volatile private var shellLoaded: CompletableDeferred<Unit>? = null
    @Volatile private var pending: CompletableDeferred<Counts>? = null
    @Volatile private var expected: Int = 0
    private val progress = Channel<Int>(Channel.CONFLATED)
    private var loadSeq = 0L
    private val mutex = Mutex()

    init {
        visibility = INVISIBLE
        isFocusable = false
        isFocusableInTouchMode = false
        importantForAccessibility = IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS
        isVerticalScrollBarEnabled = false
        isHorizontalScrollBarEnabled = false
        setPadding(0, 0, 0, 0)
        settings.apply {
            javaScriptEnabled = true
            useWideViewPort = true
            loadWithOverviewMode = true
            setSupportZoom(true)
            builtInZoomControls = true
            displayZoomControls = false
            // Parity depends on this: setting the navigator's useReadiumCssFontSize = false makes Readium drive textZoom, and breaks it.
            textZoom = READIUM_TEXT_ZOOM
            // Not layout-relevant; nothing here needs file or content URLs.
            allowFileAccess = false
            allowContentAccess = false
        }
        addJavascriptInterface(Bridge(), BRIDGE_NAME)
        webViewClient = Client()
    }

    /**
     * Counts pages for each of [hrefs] (relative to the package root) under the
     * live reader's [style] and captured [headNodes] ([LiveHeadCapture.Captured]).
     * [pubLang] is the language Readium falls back to - pass
     * `navigator.settings.value.language?.code`, which is what Readium's own
     * layout uses - and [liveDir] is [LiveHeadCapture.Captured.dir].
     *
     * Serialized: a second call waits for the first. Throws [PageCountException]
     * on a script error, or when [IDLE_TIMEOUT_MS] pass without the shell
     * loading or the script finishing another resource. On cancellation or
     * failure the view is reset to `about:blank`, which also stops the script.
     */
    suspend fun count(
        style: String,
        headNodes: List<String>,
        hrefs: List<String>,
        pubLang: String? = null,
        liveDir: String? = null,
    ): Counts = mutex.withLock {
        if (hrefs.isEmpty()) return@withLock Counts(emptyList(), emptyList())
        val prepared = PageCounterRequests.prepareHead(headNodes, liveDir)
        withContext(Dispatchers.Main.immediate) {
            val loaded = CompletableDeferred<Unit>()
            val done = CompletableDeferred<Counts>()
            val url = PageCounterRequests.shellUrl(++loadSeq)
            shell = Shell(url, PageCounterRequests.shellHtml(style, prepared.nodes))
            expected = hrefs.size
            shellLoaded = loaded
            while (progress.tryReceive().isSuccess) Unit
            try {
                loadUrl(url)
                withTimeoutOrNull(IDLE_TIMEOUT_MS) { loaded.await() }
                    ?: throw PageCountException("page counter shell did not load in ${IDLE_TIMEOUT_MS / 1000} s")
                // Only now accept bridge calls: the fresh shell has replaced
                // any earlier page, so a late callback from it cannot land here.
                pending = done
                val options = PageCounterRequests.CounterOptions(pubLang = pubLang, dir = prepared.forcedDir)
                evaluateJavascript(PageCounterRequests.countScript(hrefs, options), null)
                awaitWithIdleTimeout(done)
            } finally {
                pending = null
                shellLoaded = null
                shell = null
                // Frees the book content, and stops the script if it is still running.
                loadUrl(BLANK)
            }
        }
    }

    private class Finished(val counts: Counts)

    /** Waits for [done], failing only if [IDLE_TIMEOUT_MS] pass with no `onProgress` from the script. */
    private suspend fun awaitWithIdleTimeout(done: CompletableDeferred<Counts>): Counts {
        while (true) {
            val next = withTimeoutOrNull(IDLE_TIMEOUT_MS) {
                select<Any> {
                    done.onAwait { Finished(it) }
                    progress.onReceive { it }
                }
            } ?: throw PageCountException("page counter made no progress in ${IDLE_TIMEOUT_MS / 1000} s")
            if (next is Finished) return next.counts
        }
    }

    /** JS bridge; its methods run on the WebView's JavaBridge thread. */
    private inner class Bridge {
        @JavascriptInterface
        fun onProgress(done: Int) {
            if (pending != null) progress.trySend(done)
        }

        @JavascriptInterface
        fun onDone(json: String) {
            val deferred = pending ?: return
            val counts = PageCounterRequests.parseCounts(json, expected)
            if (counts != null) {
                deferred.complete(counts)
            } else {
                deferred.completeExceptionally(PageCountException("unusable page count result"))
            }
        }

        @JavascriptInterface
        fun onError(msg: String) {
            pending?.completeExceptionally(PageCountException("page counter failed: ${msg.take(500)}"))
        }
    }

    private inner class Client : WebViewClient() {
        override fun onPageFinished(view: WebView, url: String?) {
            if (url != null && url == shell?.url) shellLoaded?.complete(Unit)
        }

        override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
            if (request.isForMainFrame && request.url.toString() == shell?.url) {
                shellLoaded?.completeExceptionally(PageCountException("shell failed to load: ${error.description}"))
            }
        }

        // Nothing in the counter may navigate: loadUrl() does not pass through
        // here, so this blocks only page-initiated navigation (links, refreshes).
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean = true

        override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? =
            when (val route = PageCounterRequests.route(request.url.toString())) {
                is PageCounterRequests.Route.Package -> servePackage(route.path)
                is PageCounterRequests.Route.Asset -> serveAsset(route.path)
                PageCounterRequests.Route.Shell ->
                    shell?.let { respond("text/html", "utf-8", it.html.toByteArray(Charsets.UTF_8)) } ?: notFound()
                PageCounterRequests.Route.None -> null
            }
    }

    private fun servePackage(path: String): WebResourceResponse {
        val url = Url(path) ?: return notFound()
        val link = publication.linkWithHref(url)
        // shouldInterceptRequest already runs on one of WebView's background IO
        // threads, never the main thread, and Readium's Resource.read() is a
        // suspend function, so blocking this thread on it is acceptable here.
        val bytes = try {
            runBlocking {
                val resource = publication.get(url) ?: return@runBlocking null
                try {
                    resource.read().getOrNull()
                } finally {
                    resource.close()
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "Could not read $path", e)
            null
        } ?: return notFound()
        val mediaType = link?.mediaType
        val mime = mediaType?.let { "${it.type}/${it.subtype}" } ?: PageCounterRequests.mediaTypeFor(path)
        return respond(mime, mediaType?.charset?.name(), bytes)
    }

    private fun serveAsset(path: String): WebResourceResponse = try {
        WebResourceResponse(
            PageCounterRequests.mediaTypeFor(path), null, 200, "OK", CORS, context.assets.open(path),
        )
    } catch (e: IOException) {
        notFound()
    }

    private fun respond(mime: String, charset: String?, bytes: ByteArray) =
        WebResourceResponse(mime, charset, 200, "OK", CORS, ByteArrayInputStream(bytes))

    private fun notFound() =
        WebResourceResponse("text/plain", "utf-8", 404, "Not Found", CORS, ByteArrayInputStream(ByteArray(0)))

    class PageCountException(message: String) : Exception(message)

    companion object {
        private const val TAG = "PageCounterWebView"
        const val BRIDGE_NAME = "TandemPageCounter"
        /** How long a count may go without the shell loading or another resource finishing. */
        const val IDLE_TIMEOUT_MS = 30_000L
        private const val BLANK = "about:blank"

        /** Readium's `R2EpubPageFragment.textZoom` default; see the class KDoc. */
        private const val READIUM_TEXT_ZOOM = 100

        /**
         * Readium's server allows any origin on its responses; fonts loaded
         * from `readium_assets` by a `readium_package` document need it.
         */
        private val CORS = mapOf("Access-Control-Allow-Origin" to "*")
    }
}
