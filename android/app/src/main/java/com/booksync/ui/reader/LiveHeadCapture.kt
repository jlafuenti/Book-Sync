package com.booksync.ui.reader

import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject

/**
 * Issue #730, task 11. The hidden [PageCounterWebView] has to lay pages out
 * exactly like Readium's live reader, but Readium's CSS injection is internal.
 * So we copy it from the live page instead: the `<html style>` attribute (every
 * ReadiumCSS user setting, as CSS variables) and the head nodes Readium added -
 * its stylesheet links, font-face links and `<style>` blocks, and the viewport
 * `<meta>` its script appends.
 *
 * "Added by Readium" means present in the live head and absent from the raw
 * resource's head: [injectedNodes] is that diff, and the JS in [script] mirrors
 * it. `<script>` nodes are never captured - the counter must not run
 * `readium-reflowable.js`.
 */
object LiveHeadCapture {

    private val json = Json { ignoreUnknownKeys = true }

    /**
     * What [script] yields: the live `<html style>`, the injected head nodes'
     * HTML, and the live `<html dir>` - which Readium forces publication-wide
     * for every stylesheet set except CJK vertical (see
     * [PageCounterRequests.prepareHead]).
     */
    data class Captured(val style: String, val head: List<String>, val dir: String? = null)

    /**
     * The live head nodes Readium injected, in live order. Each raw node
     * cancels one equal live node (a multiset difference), so a node Readium
     * injects that happens to equal one of the book's own still survives.
     * Given live `[A, R1, B, R2]` and raw `[A, B]`, returns `[R1, R2]`.
     */
    fun injectedNodes(live: List<String>, raw: List<String>): List<String> {
        val pool = raw.groupingBy { it }.eachCount().toMutableMap()
        return live.filter { node ->
            val left = pool[node] ?: 0
            if (left > 0) {
                pool[node] = left - 1
                false
            } else {
                true
            }
        }
    }

    /**
     * JS for `EpubNavigatorFragment.evaluateJavascript` on the LIVE reader,
     * showing the resource whose raw markup is [rawHeadHtml] (the whole
     * resource, or just its head - either parses). Returns
     * `JSON.stringify({style, dir, head})`; decode it with [parse].
     *
     * Nodes are compared by a normalized key (tag, sorted attributes minus
     * `xmlns`, trimmed text) rather than raw outerHTML: an XHTML resource is
     * live as an XML document, where outerHTML serializes differently from the
     * same node parsed out of the raw text. The captured nodes are
     * re-serialized through an HTML document, so the shell (served as
     * `text/html`) never receives XML-only syntax such as a self-closed
     * `<style/>`.
     *
     * [rawHeadHtml] goes in as a JSON string literal built by
     * kotlinx.serialization, never by concatenation.
     */
    fun script(rawHeadHtml: String): String {
        val rawLiteral = Json.encodeToString(String.serializer(), rawHeadHtml).replace("</", "<\\/")
        return """
            (() => {
              const RAW = $rawLiteral;
              const key = n => n.localName.toLowerCase() + '|' +
                [...n.attributes].filter(a => !/^xmlns(:|${'$'})/.test(a.name))
                  .map(a => a.name.toLowerCase() + '=' + a.value).sort().join('|') +
                '|' + (n.textContent || '').trim();
              let rawDoc = null;
              if (document.contentType === 'application/xhtml+xml') {
                const d = new DOMParser().parseFromString(RAW, 'application/xhtml+xml');
                if (!d.getElementsByTagName('parsererror').length) rawDoc = d;
              }
              if (!rawDoc) rawDoc = new DOMParser().parseFromString(RAW, 'text/html');
              const rawHead = rawDoc.head || rawDoc.getElementsByTagName('head')[0];
              const pool = new Map();
              for (const n of (rawHead ? [...rawHead.children] : [])) {
                const k = key(n); pool.set(k, (pool.get(k) || 0) + 1);
              }
              const htmlDoc = document.implementation.createHTMLDocument('');
              const head = [];
              for (const n of [...document.head.children]) {
                if (n.localName.toLowerCase() === 'script') continue;
                const k = key(n), left = pool.get(k) || 0;
                if (left > 0) { pool.set(k, left - 1); continue; }
                head.push(htmlDoc.importNode(n, true).outerHTML);
              }
              const root = document.documentElement;
              return JSON.stringify({ style: root.getAttribute('style') || '', dir: root.getAttribute('dir'), head });
            })()
        """.trimIndent()
    }

    /**
     * Decodes what `evaluateJavascript` hands back for [script]: normally a
     * JSON string literal wrapping the object (the script returns a string,
     * which the bridge JSON-encodes again), or the plain object. `null`, empty,
     * malformed input or a missing `head` array return `null`; a missing style
     * reads as empty, a missing dir as `null`.
     */
    fun parse(result: String?): Captured? {
        val trimmed = result?.trim().orEmpty()
        if (trimmed.isEmpty() || trimmed == "null") return null
        return try {
            val element = json.parseToJsonElement(trimmed)
            val obj = if (element is JsonPrimitive && element.isString) {
                json.parseToJsonElement(element.content).jsonObject
            } else {
                element.jsonObject
            }
            val head = obj["head"] as? JsonArray ?: return null
            val style = (obj["style"] as? JsonPrimitive)?.takeIf { it !is JsonNull }?.content.orEmpty()
            val dir = (obj["dir"] as? JsonPrimitive)?.takeIf { it !is JsonNull }?.content
            Captured(style, head.map { (it as JsonPrimitive).content }, dir)
        } catch (e: Exception) {
            null
        }
    }
}
