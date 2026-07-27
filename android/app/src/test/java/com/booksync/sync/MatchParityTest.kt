package com.booksync.sync

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.float
import kotlinx.serialization.json.int
import kotlinx.serialization.json.contentOrNull
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
 * Cross-platform parity, phase 2 (issues #41 / #82): SyncMatcher.match must pick exactly
 * the same sync point as the server's services/sync_matcher.py::match_text_to_sync_points
 * for every shared golden vector in server/tests/fixtures/sync_parity/match_cases.json
 * (copied onto the test classpath by the copySyncParityFixtures Gradle task).
 */
class MatchParityTest {

    private data class Point(
        override val epubChapter: Int,
        override val epubSentenceIndex: Int,
        override val epubTextPreview: String?,
        override val confidence: Float,
    ) : MatchablePoint

    @Test
    fun match_matches_server_golden_vectors() {
        val stream = javaClass.classLoader
            ?.getResourceAsStream("sync_parity/match_cases.json")
            ?: error("match_cases.json not on the test classpath — the copySyncParityFixtures task must run")

        val text = stream.bufferedReader(Charsets.UTF_8).use { it.readText() }
        val cases = Json.parseToJsonElement(text).jsonArray
        assertTrue("expected at least one golden vector", cases.size > 0)

        for (case in cases) {
            val obj = case.jsonObject
            val name = obj["name"]!!.jsonPrimitive.content
            val points = obj["sync_points"]!!.jsonArray.map { element ->
                val p = element.jsonObject
                Point(
                    epubChapter = p["chapter"]!!.jsonPrimitive.int,
                    epubSentenceIndex = p["sentence_index"]!!.jsonPrimitive.int,
                    // contentOrNull, not content — a JSON null preview must stay null,
                    // not become the string "null".
                    epubTextPreview = p["preview"]?.jsonPrimitive?.contentOrNull,
                    // Absent means "fully confident" — cases that don't exercise the
                    // interpolation nudge omit the key.
                    confidence = p["confidence"]?.jsonPrimitive?.float ?: 1.0f,
                )
            }

            val result = SyncMatcher.match(
                points,
                obj["epub_text"]!!.jsonPrimitive.content,
                obj["chapter_hint"]!!.jsonPrimitive.int,
            )

            val expectedSentence = obj["expected_sentence_index"]!!.jsonPrimitive.intOrNull
            if (expectedSentence == null) {
                assertNull("case '$name' should not match", result)
            } else {
                assertNotNull("case '$name' should match", result)
                assertEquals(
                    "case '$name' matched the wrong chapter",
                    obj["expected_chapter"]!!.jsonPrimitive.int,
                    result!!.epubChapter,
                )
                assertEquals(
                    "case '$name' matched the wrong sentence",
                    expectedSentence,
                    result.epubSentenceIndex,
                )
            }
        }
    }

    @Test
    fun fuzzy_pass_rejects_a_needle_longer_than_the_transcript() {
        val points = listOf(Point(1, 0, "a short preview", 1.0f))
        assertNull(
            SyncMatcher.match(points, "a much longer stretch of extracted text than the transcript holds", 1),
        )
    }

    @Test
    fun interpolated_point_without_confident_neighbour_is_kept() {
        val points = listOf(
            Point(1, 0, "The caravan left the city gates before the sun had fully risen today", 0.2f),
            Point(1, 1, "They traveled north along the river until the road turned sharply east", 0.0f),
        )
        val result = SyncMatcher.match(points, "They traveled north along the river until the road turned sharply east.", 1)
        assertEquals(1, result?.epubSentenceIndex)
    }
}
