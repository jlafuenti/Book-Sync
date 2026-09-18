package com.booksync.data.remote

import kotlinx.serialization.Serializable
import retrofit2.http.GET
import retrofit2.http.Path

/**
 * Wikimedia's Wiktionary REST API definition endpoint
 * (`https://en.wiktionary.org/api/rest_v1/page/definition/{word}`).
 * No auth, no API key, Wikimedia-run — see issue #608, which replaced
 * `api.dictionaryapi.dev` as the primary lookup after it was measured at
 * ~20s round trips against Wiktionary's ~0.3s.
 *
 * The response is a JSON object keyed by language code — `"en"` for
 * English, plus whichever other languages also happen to define the same
 * page title — each holding a list of part-of-speech entries. We only look
 * at the `"en"` entry; a page that exists but has no English section (e.g.
 * a French-only word) is treated the same as a 404, which
 * [com.booksync.data.repository.DictionaryRepository] falls back on.
 *
 * `definition` and `examples` strings contain raw Wiktionary HTML (wiki
 * links, `<i>` tags for taxonomic names, etc.) and must be stripped to plain
 * text before display — see `DictionaryRepository.stripHtml`. We deliberately
 * deserialize only the subset we render; `Json` is configured with
 * `ignoreUnknownKeys = true` in [com.booksync.di.AppModule] so the several
 * other fields in the real payload (`parsedExamples`, `language`
 * sub-objects, etc.) don't break parsing.
 */
interface WiktionaryApi {
    @GET("api/rest_v1/page/definition/{word}")
    suspend fun lookup(@Path("word") word: String): Map<String, List<WiktionaryEntry>>
}

@Serializable
data class WiktionaryEntry(
    val partOfSpeech: String,
    val definitions: List<WiktionaryDefinition> = emptyList(),
)

@Serializable
data class WiktionaryDefinition(
    val definition: String,
    val examples: List<String> = emptyList(),
)
