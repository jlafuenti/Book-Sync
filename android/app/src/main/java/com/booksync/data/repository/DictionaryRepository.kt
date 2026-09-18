package com.booksync.data.repository

import com.booksync.data.remote.DictionaryApi
import com.booksync.data.remote.DictionaryDefinition
import com.booksync.data.remote.DictionaryEntry
import com.booksync.data.remote.DictionaryMeaning
import com.booksync.data.remote.WiktionaryApi
import java.io.IOException
import java.util.LinkedHashMap
import javax.inject.Inject
import javax.inject.Singleton
import org.jsoup.Jsoup
import retrofit2.HttpException

/**
 * Looks up word definitions, preferring Wiktionary over `dictionaryapi.dev`,
 * with a tiny in-memory LRU cache so repeat lookups of the same word (in the
 * same reading session) don't hit the network twice. Cache is process-lifetime
 * only and intentionally not persisted — issue #608 asked to fix the lookup
 * itself, not add durable caching.
 *
 * ## Source order (issue #608)
 *
 * `dictionaryapi.dev` — the original and only source — is a hobby-run free
 * API that was measured taking ~20s to respond, well past any timeout we can
 * afford on a tap-to-define interaction. [WiktionaryApi] (Wikimedia-run, no
 * key, ~0.3s in the same measurement) is now the primary source; the old API
 * stays as a fallback for the words Wiktionary's English section doesn't
 * cover (chiefly inflected forms Wiktionary sometimes files only under the
 * lemma).
 *
 * Both requests get one retry on a timeout or other I/O failure before the
 * lookup gives up — see [withRetry]. A non-2xx/404 [HttpException] is not
 * retried: it's a definite answer ("this word doesn't exist here"), not a
 * transient failure.
 */
@Singleton
class DictionaryRepository @Inject constructor(
    private val wiktionaryApi: WiktionaryApi,
    private val fallbackApi: DictionaryApi,
) {
    private val cacheCapacity = 16
    private val cache = object : LinkedHashMap<String, List<DictionaryEntry>>(
        cacheCapacity, 0.75f, /* accessOrder = */ true,
    ) {
        override fun removeEldestEntry(eldest: Map.Entry<String, List<DictionaryEntry>>): Boolean =
            size > cacheCapacity
    }

    /**
     * Returns the dictionary entries for [word], or an empty list if the word
     * isn't in the dictionary (Wiktionary has no English section for it *and*
     * the fallback returns nothing either). Throws on network/timeout errors
     * so the caller can distinguish "offline / lookup failed" from
     * "not a real word" — see `ReaderActivity.defineSelectedWord`, which shows
     * a different toast for each.
     */
    suspend fun lookup(word: String): List<DictionaryEntry> {
        val key = word.trim().lowercase()
        if (key.isEmpty()) return emptyList()

        synchronized(cache) { cache[key] }?.let { return it }

        val entries = lookupWiktionary(key) ?: lookupFallback(key)

        synchronized(cache) { cache[key] = entries }
        return entries
    }

    /**
     * Null means "no English entry" (404, or a page that exists only in
     * other languages) — the signal to fall back to [fallbackApi]. A timeout
     * or other I/O failure that survives [withRetry] propagates instead of
     * falling back: that mirrors the pre-#608 contract (throw = lookup
     * failed) rather than silently masking an outage behind a slower fallback
     * that would likely fail the same way.
     */
    private suspend fun lookupWiktionary(word: String): List<DictionaryEntry>? {
        val response = try {
            withRetry { wiktionaryApi.lookup(word) }
        } catch (e: HttpException) {
            if (e.code() == 404) return null else throw e
        }

        val english = response["en"]
        if (english.isNullOrEmpty()) return null

        return listOf(
            DictionaryEntry(
                word = word,
                phonetic = null,
                meanings = english.map { entry ->
                    DictionaryMeaning(
                        partOfSpeech = entry.partOfSpeech,
                        definitions = entry.definitions.map { def ->
                            DictionaryDefinition(
                                definition = stripHtml(def.definition),
                                example = def.examples.firstOrNull()?.let(::stripHtml)?.takeIf(String::isNotBlank),
                            )
                        },
                    )
                },
            ),
        )
    }

    private suspend fun lookupFallback(word: String): List<DictionaryEntry> =
        try {
            withRetry { fallbackApi.lookup(word) }
        } catch (e: HttpException) {
            if (e.code() == 404) emptyList() else throw e
        }

    /** Strips HTML tags and decodes entities, leaving plain display text. */
    private fun stripHtml(html: String): String = Jsoup.parse(html).text()

    /**
     * Runs [block] once, and again exactly once more if the first attempt
     * fails with an [IOException] (covers `SocketTimeoutException` along with
     * DNS/connection failures) — see issue #608: the dictionary APIs are
     * occasionally slow rather than reliably down, so one retry recovers a
     * lookup that would otherwise report "lookup failed" for a transient
     * blip. A second failure propagates.
     */
    private suspend fun <T> withRetry(block: suspend () -> T): T =
        try {
            block()
        } catch (e: IOException) {
            block()
        }
}
