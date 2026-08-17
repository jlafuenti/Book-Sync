package com.booksync.ui.reader

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

/**
 * Text read out of the reader's WebView, and *how* it was obtained (issue #131).
 *
 * The extractor has two paths. The DOM read walks the current CSS column and
 * returns real page text. The `scrollBasedText` fallback slices `body.innerText`
 * at a character offset derived from the horizontal scroll fraction — and since
 * Readium paginates with columns, character offset does not track page position,
 * so that slice regularly names text from a different page.
 *
 * Both used to come back as a bare `String`, so the caller seeked to a guess with
 * the same confidence as a real read. Carrying the provenance lets the handoff
 * decline to claim precision it does not have.
 */
data class VisibleText(
    val text: String,
    val source: Source,
) {
    enum class Source { DOM, ESTIMATED, NONE }

    /** True only for a non-blank DOM read — the one case worth seeking on. */
    val isPrecise: Boolean get() = source == Source.DOM && text.isNotBlank()

    companion object {
        val EMPTY = VisibleText("", Source.NONE)

        private val json = Json { ignoreUnknownKeys = true }

        /**
         * Parse what `evaluateJavascript` hands back.
         *
         * It arrives double-encoded — the JS returns `JSON.stringify(...)`, and
         * the bridge then encodes *that string* as JSON — so the payload is
         * usually a JSON string literal wrapping a JSON object. Both shapes are
         * accepted so the decode is one testable step rather than an unwrap on
         * the device plus a parse here.
         *
         * `kotlinx.serialization`, not `org.json`: the latter is an android.jar
         * stub under unit tests (`unitTests.isReturnDefaultValues = true`), so
         * every parse would silently return empty and prove nothing.
         *
         * Anything unrecognised is an estimate at best — a wrong seek presented
         * as exact is worse than no seek, so the unsafe reading is never the
         * default.
         */
        fun parse(raw: String?): VisibleText {
            val trimmed = raw?.trim().orEmpty()
            if (trimmed.isEmpty() || trimmed == "null") return EMPTY
            return try {
                val obj = decodeObject(trimmed) ?: return EMPTY
                val text = (obj["text"] as? JsonPrimitive)?.content.orEmpty()
                    .replace("\n", " ")
                    .replace("\t", " ")
                    .trim()
                if (text.isEmpty()) return EMPTY
                val source = when ((obj["source"] as? JsonPrimitive)?.content) {
                    "dom" -> Source.DOM
                    else -> Source.ESTIMATED
                }
                VisibleText(text, source)
            } catch (e: Exception) {
                EMPTY
            }
        }

        /** Accepts `{...}` or `"{...}"` (the bridge's double encoding). */
        private fun decodeObject(raw: String): JsonObject? {
            val element = json.parseToJsonElement(raw)
            val primitive = element as? JsonPrimitive
            return if (primitive != null && primitive.isString) {
                json.parseToJsonElement(primitive.content).jsonObject
            } else {
                element.jsonObject
            }
        }
    }
}
