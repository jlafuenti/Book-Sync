package com.booksync.data.remote

import kotlinx.serialization.Serializable
import retrofit2.http.GET
import retrofit2.http.Path

/**
 * Free public dictionary at https://api.dictionaryapi.dev/.
 * No auth required. Returns 404 when a word isn't found.
 *
 * The real payload contains many fields (audio URLs, multiple phonetics,
 * synonyms, antonyms, sourceUrls, license…). We deliberately deserialize only
 * the subset we render so a schema change on their end doesn't break us —
 * Json is configured with ignoreUnknownKeys = true in [com.booksync.di.AppModule].
 */
interface DictionaryApi {
    @GET("api/v2/entries/en/{word}")
    suspend fun lookup(@Path("word") word: String): List<DictionaryEntry>
}

@Serializable
data class DictionaryEntry(
    val word: String,
    val phonetic: String? = null,
    val meanings: List<DictionaryMeaning> = emptyList(),
)

@Serializable
data class DictionaryMeaning(
    val partOfSpeech: String,
    val definitions: List<DictionaryDefinition> = emptyList(),
)

@Serializable
data class DictionaryDefinition(
    val definition: String,
    val example: String? = null,
)
