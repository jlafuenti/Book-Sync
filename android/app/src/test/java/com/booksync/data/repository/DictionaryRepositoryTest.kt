package com.booksync.data.repository

import com.booksync.data.remote.DictionaryApi
import com.booksync.data.remote.DictionaryDefinition
import com.booksync.data.remote.DictionaryEntry
import com.booksync.data.remote.DictionaryMeaning
import com.booksync.data.remote.WiktionaryApi
import com.booksync.data.remote.WiktionaryDefinition
import com.booksync.data.remote.WiktionaryEntry
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import kotlinx.coroutines.test.runTest
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import retrofit2.HttpException
import retrofit2.Response
import java.net.SocketTimeoutException

/**
 * Tests for [DictionaryRepository] (issue #608): Wiktionary is now the
 * primary lookup source (fast, Wikimedia-run) with `dictionaryapi.dev` kept
 * only as a fallback for words Wiktionary's `"en"` section doesn't cover.
 * No live network — both Retrofit interfaces are mocked.
 */
class DictionaryRepositoryTest {

    private val wiktionaryApi = mockk<WiktionaryApi>()
    private val fallbackApi = mockk<DictionaryApi>()

    private fun repository() = DictionaryRepository(wiktionaryApi, fallbackApi)

    private fun http404() = HttpException(Response.error<Unit>(404, "".toResponseBody(null)))

    private fun http(code: Int) = HttpException(Response.error<Unit>(code, "".toResponseBody(null)))

    @Test
    fun `a refused Wiktionary request falls back instead of failing the lookup`() = runTest {
        // Wikimedia answers 403 when the agent string displeases its robot
        // policy; a 404-only fallback turned that into "lookup failed" for
        // every word (issue #608 follow-up).
        coEvery { wiktionaryApi.lookup("graveyard") } throws http(403)
        coEvery { fallbackApi.lookup("graveyard") } returns listOf(
            DictionaryEntry(
                word = "graveyard",
                phonetic = null,
                meanings = listOf(
                    DictionaryMeaning("noun", listOf(DictionaryDefinition("a burial ground", null))),
                ),
            ),
        )

        val entries = repository().lookup("graveyard")

        assertEquals("a burial ground", entries.single().meanings.single().definitions.single().definition)
        coVerify(exactly = 1) { fallbackApi.lookup("graveyard") }
    }

    @Test
    fun `a Wiktionary server error falls back`() = runTest {
        coEvery { wiktionaryApi.lookup("hello") } throws http(503)
        coEvery { fallbackApi.lookup("hello") } returns emptyList()

        assertTrue(repository().lookup("hello").isEmpty())

        coVerify(exactly = 1) { fallbackApi.lookup("hello") }
    }

    @Test
    fun `a Wiktionary timeout that survives the retry falls back`() = runTest {
        coEvery { wiktionaryApi.lookup("water") } throws SocketTimeoutException("timeout")
        coEvery { fallbackApi.lookup("water") } returns listOf(
            DictionaryEntry(
                word = "water",
                phonetic = null,
                meanings = listOf(
                    DictionaryMeaning("noun", listOf(DictionaryDefinition("a clear liquid", null))),
                ),
            ),
        )

        val entries = repository().lookup("water")

        assertEquals("a clear liquid", entries.single().meanings.single().definitions.single().definition)
        coVerify(exactly = 2) { wiktionaryApi.lookup("water") }
        coVerify(exactly = 1) { fallbackApi.lookup("water") }
    }

    @Test
    fun `both sources failing still reports a failed lookup`() = runTest {
        coEvery { wiktionaryApi.lookup("stone") } throws http(403)
        coEvery { fallbackApi.lookup("stone") } throws SocketTimeoutException("timeout")

        var threw = false
        try {
            repository().lookup("stone")
        } catch (e: Exception) {
            threw = true
        }

        assertTrue("a lookup with no working source must report failure", threw)
    }

    @Test
    fun `lookup parses multiple parts of speech from Wiktionary`() = runTest {
        coEvery { wiktionaryApi.lookup("run") } returns mapOf(
            "en" to listOf(
                WiktionaryEntry(
                    partOfSpeech = "Noun",
                    definitions = listOf(WiktionaryDefinition("an act of running", listOf("a run in the park"))),
                ),
                WiktionaryEntry(
                    partOfSpeech = "Verb",
                    definitions = listOf(WiktionaryDefinition("to move at a pace faster than a walk")),
                ),
            ),
        )

        val entries = repository().lookup("run")

        assertEquals(1, entries.size)
        val meanings = entries.single().meanings
        assertEquals(listOf("Noun", "Verb"), meanings.map { it.partOfSpeech })
        assertEquals("an act of running", meanings[0].definitions.single().definition)
        assertEquals("a run in the park", meanings[0].definitions.single().example)
        assertEquals("to move at a pace faster than a walk", meanings[1].definitions.single().definition)
    }

    @Test
    fun `lookup strips HTML tags and decodes entities from Wiktionary text`() = runTest {
        coEvery { wiktionaryApi.lookup("fish") } returns mapOf(
            "en" to listOf(
                WiktionaryEntry(
                    partOfSpeech = "Noun",
                    definitions = listOf(
                        WiktionaryDefinition(
                            definition = "A cold-blooded <a href=\"/wiki/vertebrate\">vertebrate</a> animal, " +
                                "e.g. a <i>salmon</i> &amp; a <i>trout</i>",
                            examples = listOf("We caught three <b>fish</b> today"),
                        ),
                    ),
                ),
            ),
        )

        val entries = repository().lookup("fish")

        val def = entries.single().meanings.single().definitions.single()
        assertEquals("A cold-blooded vertebrate animal, e.g. a salmon & a trout", def.definition)
        assertEquals("We caught three fish today", def.example)
        assertTrue("no angle brackets should remain", def.definition.none { it == '<' || it == '>' })
    }

    @Test
    fun `lookup falls back to dictionaryapi dev when Wiktionary has no English section`() = runTest {
        coEvery { wiktionaryApi.lookup("motoneige") } returns mapOf("fr" to listOf(WiktionaryEntry("Noun")))
        coEvery { fallbackApi.lookup("motoneige") } returns listOf(
            DictionaryEntry(
                word = "motoneige",
                meanings = listOf(DictionaryMeaning("noun", listOf(DictionaryDefinition("a snowmobile")))),
            ),
        )

        val entries = repository().lookup("motoneige")

        assertEquals("motoneige", entries.single().word)
        assertEquals("a snowmobile", entries.single().meanings.single().definitions.single().definition)
    }

    @Test
    fun `lookup falls back to dictionaryapi dev on Wiktionary 404`() = runTest {
        coEvery { wiktionaryApi.lookup("xyzzy") } throws http404()
        coEvery { fallbackApi.lookup("xyzzy") } returns listOf(
            DictionaryEntry(word = "xyzzy", meanings = listOf(DictionaryMeaning("noun", listOf(DictionaryDefinition("a magic word"))))),
        )

        val entries = repository().lookup("xyzzy")

        assertEquals(1, entries.size)
        assertEquals("a magic word", entries.single().meanings.single().definitions.single().definition)
    }

    @Test
    fun `lookup reports no entry when both Wiktionary and the fallback have nothing`() = runTest {
        coEvery { wiktionaryApi.lookup("qzxnoword") } throws http404()
        coEvery { fallbackApi.lookup("qzxnoword") } throws http404()

        val entries = repository().lookup("qzxnoword")

        assertEquals(emptyList<DictionaryEntry>(), entries)
    }

    @Test
    fun `lookup retries once on timeout and succeeds on the second attempt`() = runTest {
        coEvery { wiktionaryApi.lookup("word") } throws SocketTimeoutException("timeout") andThen mapOf(
            "en" to listOf(WiktionaryEntry("Noun", listOf(WiktionaryDefinition("a unit of language")))),
        )

        val entries = repository().lookup("word")

        assertEquals("a unit of language", entries.single().meanings.single().definitions.single().definition)
        coVerify(exactly = 2) { wiktionaryApi.lookup("word") }
    }

    @Test
    fun `lookup fails after one retry per source when timeouts persist`() = runTest {
        // Each source is tried twice (one retry); the lookup only fails once
        // both have been exhausted — a Wiktionary timeout alone falls back.
        coEvery { wiktionaryApi.lookup("word") } throws SocketTimeoutException("timeout")
        coEvery { fallbackApi.lookup("word") } throws SocketTimeoutException("timeout")

        var caught: Throwable? = null
        try {
            repository().lookup("word")
        } catch (e: SocketTimeoutException) {
            caught = e
        }

        assertTrue("expected lookup to throw SocketTimeoutException", caught is SocketTimeoutException)
        coVerify(exactly = 2) { wiktionaryApi.lookup("word") }
        coVerify(exactly = 2) { fallbackApi.lookup("word") }
    }
}
