package com.booksync.sync

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.int
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Cross-platform parity for audio -> epub point selection (issue #200).
 *
 * [SyncMatcher.pointForAudioPosition] must pick the same point as the server's
 * `services/sync_engine.audio_to_epub` for every shared golden vector in
 * server/tests/fixtures/sync_parity/audio_to_epub_cases.json (copied onto the
 * test classpath by the copySyncParityFixtures Gradle task).
 *
 * The case this exists for: a position earlier than every point. Both platforms
 * used to answer "chapter 0" — the server (0, 0), Android `Pair(0, "")` — which
 * is indistinguishable from a real hit on the opening sentence, and let a
 * re-map silently relocate a bookmark to the start of the book.
 */
class AudioToEpubParityTest {

    private data class Point(
        val chapter: Int,
        val sentenceIndex: Int,
        val audioStartMs: Int,
    )

    private fun loadCases() = javaClass.classLoader
        ?.getResourceAsStream("sync_parity/audio_to_epub_cases.json")
        ?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }
        ?.let { Json.parseToJsonElement(it).jsonArray }
        ?: error("audio_to_epub_cases.json not on the test classpath — copySyncParityFixtures must run")

    @Test
    fun pointForAudioPosition_matches_server_golden_vectors() {
        val cases = loadCases()
        assertTrue("expected at least one golden vector", cases.size > 0)

        for (case in cases) {
            val obj = case.jsonObject
            val name = obj["name"]!!.jsonPrimitive.content
            val why = obj["why"]?.jsonPrimitive?.contentOrNull ?: ""
            val points = obj["points"]!!.jsonArray.map {
                val o = it.jsonObject
                Point(
                    chapter = o["chapter"]!!.jsonPrimitive.int,
                    sentenceIndex = o["sentence_index"]!!.jsonPrimitive.int,
                    audioStartMs = o["audio_start_ms"]!!.jsonPrimitive.int,
                )
            }

            val chosen = SyncMatcher.pointForAudioPosition(
                points, obj["audio_position_ms"]!!.jsonPrimitive.int
            ) { it.audioStartMs }

            val expectedIndex = obj["expected_point_index"]!!.jsonPrimitive.intOrNull
            if (expectedIndex == null) {
                assertNull("$name: $why", chosen)
            } else {
                assertNotNull("$name: $why", chosen)
                assertEquals("$name: $why", points[expectedIndex], chosen)
            }
        }
    }

    @Test
    fun an_empty_map_names_no_point() {
        assertNull(SyncMatcher.pointForAudioPosition(emptyList<Point>(), 5_000) { it.audioStartMs })
    }
}
