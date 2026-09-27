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
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonObject
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

    /**
     * Starts a count in the loaded shell. [hrefs] are reading-order hrefs
     * relative to the package root (Readium's `Link.url().toString()`), passed
     * as a JSON array literal built by kotlinx.serialization. The result comes
     * back through the `TandemPageCounter` bridge, not as the script's value.
     */
    fun countScript(hrefs: List<String>): String {
        val literal = Json.encodeToString(ListSerializer(String.serializer()), hrefs).replace("</", "<\\/")
        return """
            (function () {
              if (typeof window.tandemCountPages !== 'function') {
                TandemPageCounter.onError('page-counter.js did not load');
                return;
              }
              window.tandemCountPages($literal);
            })();
        """.trimIndent()
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
 * load - reusing one document across a settings change left stale layout
 * behind in the spike.
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

    private class Shell(val html: String)

    @Volatile private var shell: Shell? = null
    @Volatile private var shellLoaded: CompletableDeferred<Unit>? = null
    @Volatile private var pending: CompletableDeferred<Counts>? = null
    @Volatile private var expected: Int = 0
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
     * live reader's [style] and injected [headNodes]. Serialized: a second call
     * waits for the first. Throws [PageCountException] on a script error or
     * after [COUNT_TIMEOUT_MS]; on cancellation or failure the view is reset to
     * `about:blank`, which also stops the script.
     */
    suspend fun count(style: String, headNodes: List<String>, hrefs: List<String>): Counts = mutex.withLock {
        if (hrefs.isEmpty()) return@withLock Counts(emptyList(), emptyList())
        withContext(Dispatchers.Main.immediate) {
            val loaded = CompletableDeferred<Unit>()
            val done = CompletableDeferred<Counts>()
            shell = Shell(PageCounterRequests.shellHtml(style, headNodes))
            expected = hrefs.size
            shellLoaded = loaded
            try {
                withTimeoutOrNull(COUNT_TIMEOUT_MS) {
                    loadUrl(PageCounterRequests.SHELL_URL)
                    loaded.await()
                    // Only now accept bridge calls: the fresh shell has replaced
                    // any earlier page, so a late callback from it cannot land here.
                    pending = done
                    evaluateJavascript(PageCounterRequests.countScript(hrefs), null)
                    done.await()
                } ?: throw PageCountException("page count timed out after ${COUNT_TIMEOUT_MS / 1000} s")
            } finally {
                pending = null
                shellLoaded = null
                shell = null
                // Frees the book content, and stops the script if it is still running.
                loadUrl(BLANK)
            }
        }
    }

    /** JS bridge; its methods run on the WebView's JavaBridge thread. */
    private inner class Bridge {
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
        override fun onPageFinished(view: WebView, url: String) {
            if (url.startsWith(PageCounterRequests.SHELL_URL)) shellLoaded?.complete(Unit)
        }

        override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
            if (request.isForMainFrame && request.url.toString().startsWith(PageCounterRequests.SHELL_URL)) {
                shellLoaded?.completeExceptionally(PageCountException("shell failed to load: ${error.description}"))
            }
        }

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
        const val COUNT_TIMEOUT_MS = 120_000L
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
