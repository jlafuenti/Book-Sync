package com.booksync.data.remote

import java.io.File
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** The per-word timing endpoint's wire shape (issue #836). */
class SyncMapWordsResponseTest {

    private val json = Json { ignoreUnknownKeys = true }

    @Test
    fun `decodes the word timing payload`() {
        val body = """
            {
              "sync_map_id": 9,
              "version": 3,
              "points": [
                {"epub_chapter": 2, "epub_sentence_index": 0, "word_starts": [100, 450, 900]},
                {"epub_chapter": 2, "epub_sentence_index": 4, "word_starts": []}
              ]
            }
        """.trimIndent()

        val decoded = json.decodeFromString<SyncMapWordsResponse>(body)

        assertEquals(9, decoded.sync_map_id)
        assertEquals(3, decoded.version)
        assertEquals(2, decoded.points.size)
        assertEquals(SyncPointWordsDto(2, 0, listOf(100, 450, 900)), decoded.points[0])
        assertTrue(decoded.points[1].word_starts.isEmpty())
    }

    @Test
    fun `an empty point list decodes to no words`() {
        val decoded = json.decodeFromString<SyncMapWordsResponse>(
            """{"sync_map_id": 1, "version": 1, "points": []}""",
        )
        assertTrue(decoded.points.isEmpty())
    }

    @Test
    fun `the API declares the words endpoint under the sync map path`() {
        val api = sourceOf("com/booksync/data/remote/BookSyncApi.kt")
        assertTrue(api.contains("@GET(\"api/files/syncmap/{pairId}/words\")"))
        assertTrue(api.contains("suspend fun getSyncMapWords(@Path(\"pairId\") pairId: Int): SyncMapWordsResponse"))
    }

    private fun sourceOf(relativePath: String): String {
        var dir = File("").absoluteFile
        repeat(4) {
            for (c in listOf(File(dir, "app/src/main/java/$relativePath"), File(dir, "src/main/java/$relativePath"))) {
                if (c.exists()) return c.readText()
            }
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("Could not locate $relativePath")
    }
}
