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
 * source is given up on — see [withRetry]. An HTTP error is not retried:
 * it is an answer, just not a useful one. Whatever the reason Wiktionary
 * produces no entry, the fallback is asked next; only a failure of *both*
 * sources is reported to the caller as a failed lookup.
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
     * Null means "ask [fallbackApi] instead": no English entry (404, or a
     * page that exists only in other languages), **or** any other failure of
     * this source — a refused request, a server error, a timeout that
     * survives [withRetry].
     *
     * Every failure falls back, not just 404, because the two sources fail
     * independently: Wikimedia answered 403 to every lookup once the app's
     * agent string displeased its robot policy (fixed in
     * [com.booksync.data.remote.wiktionaryUserAgent]), and with a
     * 404-only fallback that turned into "lookup failed" for every word
     * rather than a slower answer from the source that was still working.
     * A lookup only reports failure when *both* sources fail — see
     * [lookupFallback], which still throws.
     */
    private suspend fun lookupWiktionary(word: String): List<DictionaryEntry>? {
        val response = try {
            withRetry { wiktionaryApi.lookup(word) }
        } catch (e: HttpException) {
            return null
        } catch (e: IOException) {
            return null
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
