package com.booksync.ui.reader

import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

/**
 * Issue #730, task 10. Readium's `EpubNavigatorFragment` exposes a public
 * `suspend fun evaluateJavascript(script: String): String?` that runs JS in the
 * live page. `LivePageProbe` builds the JS that reads the current CSS-column
 * page and page count directly from the rendered WebView (Readium paginates
 * each resource with CSS columns), plus which page-list fragments in the
 * current resource sit at or before that column - and parses the JSON the
 * script hands back.
 *
 * `printListAt` then resolves a print-page-list label from that, mirroring
 * web's `web/src/lib/printPages.js` `printListAt` (task 7) so the two
 * platforms agree on the same page-list contract.
 */
object LivePageProbe {

    private val json = Json { ignoreUnknownKeys = true }

    /**
     * The JS to evaluate in the live page. `fragments` are element ids (from
     * `publication.pageList` hrefs in the current resource) to test against the
     * current column; the script reports which of them are at or before it.
     *
     * The `FRAGMENTS` array literal is built with kotlinx.serialization, never
     * by string concatenation, so an id containing `"` or `\` cannot break the
     * script.
     */
    fun script(fragments: List<String>): String {
        val fragmentsLiteral = Json.encodeToString(ListSerializer(String.serializer()), fragments)
        return """
            (() => {
              const se = document.scrollingElement, w = window.innerWidth;
              const col = Math.round(se.scrollLeft / w);
              const out = { page: col + 1, total: Math.max(1, Math.round(se.scrollWidth / w)), before: [] };
              const FRAGMENTS = $fragmentsLiteral;
              for (const id of FRAGMENTS) {
                const el = document.getElementById(id);
                if (el && Math.floor((el.getBoundingClientRect().left + se.scrollLeft) / w) <= col) out.before.push(id);
              }
              return JSON.stringify(out);
            })()
        """.trimIndent()
    }

    data class Result(val page: Int, val total: Int, val fragmentsBeforeOrAt: Set<String>)

    /**
     * Parse what `evaluateJavascript` hands back. It arrives double-encoded -
     * the JS returns `JSON.stringify(out)`, a string, and Android's
     * `evaluateJavascript` JSON-encodes *that string* again - so the payload is
     * normally a JSON string literal wrapping the JSON object (`"{\"page\":...}"`).
     * A plain (non-double-encoded) object is also accepted.
     *
     * `null`, the literal `"null"`, empty input, and malformed input all return
     * `null`. Results are clamped: `page >= 1`, `total >= 1`, `page <= total` -
     * anything outside that range is treated as unusable rather than trusted.
     */
    fun parse(json: String?): Result? {
        val trimmed = json?.trim().orEmpty()
        if (trimmed.isEmpty() || trimmed == "null") return null
        return try {
            val obj = decodeObject(trimmed) ?: return null
            val page = (obj["page"] as? JsonPrimitive)?.intOrNull ?: return null
            val total = (obj["total"] as? JsonPrimitive)?.intOrNull ?: return null
            if (page < 1 || total < 1 || page > total) return null
            val before = (obj["before"]?.jsonArray)
                ?.mapNotNull { (it as? JsonPrimitive)?.contentOrNull }
                ?.toSet()
                ?: emptySet()
            Result(page, total, before)
        } catch (e: Exception) {
            null
        }
    }

    /** Accepts `{...}` or `"{...}"` (the bridge's double encoding). */
    private fun decodeObject(raw: String): JsonObject? {
        val element = this.json.parseToJsonElement(raw)
        val primitive = element as? JsonPrimitive
        return if (primitive != null && primitive.isString) {
            this.json.parseToJsonElement(primitive.content).jsonObject
        } else {
            element.jsonObject
        }
    }

    /**
     * Mirrors web's `printPages.js` `printListAt`. `pageList` is
     * `(readingOrderIndex, fragment)` in book order, built from
     * `publication.pageList` hrefs mapped to `publication.readingOrder`
     * indexes; `labels` is the parallel list of raw labels.
     *
     * Only entries whose label parses as a finite integer count (epub.js uses
     * `parseInt`, matched here with `toIntOrNull`); the kept label is that
     * integer's decimal string, so a leading-zero or `+`-prefixed label is
     * normalized the same way epub.js's `parseInt` + `String(page)` round trip
     * normalizes it on the web.
     *
     * `currentLabel` is the label of the last kept entry that is in an earlier
     * section (`readingOrderIndex < sectionIndex`), or in this section
     * (`readingOrderIndex == sectionIndex`) with its fragment in `before`; it is
     * `null` when no kept entry qualifies. `firstLabel`/`lastLabel` are the
     * first and last kept labels in book order. An empty `pageList` (or one
     * with no numeric labels) returns `null` overall.
     */
    fun printListAt(
        pageList: List<Pair<Int, String>>,
        labels: List<String>,
        sectionIndex: Int,
        before: Set<String>,
    ): ReaderProgress.PrintList? {
        val kept = pageList.zip(labels).mapNotNull { (entry, label) ->
            val n = label.toIntOrNull() ?: return@mapNotNull null
            Triple(entry.first, entry.second, n.toString())
        }
        if (kept.isEmpty()) return null

        var currentLabel: String? = null
        for ((readingOrderIndex, fragment, label) in kept) {
            if (readingOrderIndex < sectionIndex) {
                currentLabel = label
            } else if (readingOrderIndex == sectionIndex && fragment in before) {
                currentLabel = label
            }
        }

        return ReaderProgress.PrintList(
            currentLabel = currentLabel,
            firstLabel = kept.first().third,
            lastLabel = kept.last().third,
        )
    }
}
