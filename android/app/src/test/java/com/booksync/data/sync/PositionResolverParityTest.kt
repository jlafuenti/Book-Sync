package com.booksync.data.sync

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.floatOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.int
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Cross-platform parity for the restore ladder: [planRestore] must produce the
 * same ordered steps as the server's `services/position_resolver.py` and the
 * web reader's `positionLadder.js` for every shared golden vector in
 * server/tests/fixtures/sync_parity/restore_cases.json (copied onto the test
 * classpath by the copySyncParityFixtures Gradle task).
 *
 * The ladder is where the three clients previously disagreed most: Android
 * restored only from a Readium locator, web only from an epub.js CFI, and
 * neither had a path back to the portable anchors both of them write.
 */
class PositionResolverParityTest {

    private fun loadCases() = javaClass.classLoader
        ?.getResourceAsStream("sync_parity/restore_cases.json")
        ?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }
        ?.let { Json.parseToJsonElement(it).jsonArray }
        ?: error("restore_cases.json not on the test classpath — copySyncParityFixtures must run")

    @Test
    fun planRestore_matches_server_golden_vectors() {
        val cases = loadCases()
        assertTrue("expected at least one golden vector", cases.size > 0)

        for (case in cases) {
            val obj = case.jsonObject
            val name = obj["name"]!!.jsonPrimitive.content
            val why = obj["why"]?.jsonPrimitive?.contentOrNull ?: ""
            val ctx = obj["context"]!!.jsonObject

            val position = parsePosition(obj["position"].toString())

            val steps = planRestore(
                position,
                spineCount = ctx["spine_count"]!!.jsonPrimitive.int,
                deviceId = ctx["device_id"]!!.jsonPrimitive.content,
                hintKind = ctx["hint_kind"]!!.jsonPrimitive.content,
            )

            val expected = obj["expected"]!!.jsonArray.map { it.jsonPrimitive.content }
            assertEquals("$name: $why", expected, steps.map { it.kind })
        }
    }

    private fun parsePosition(raw: String): StoredPosition? {
        if (raw.trim() == "null") return null
        val o = Json.parseToJsonElement(raw).jsonObject
        return StoredPosition(
            anchorRevision = o["anchor_revision"]?.jsonPrimitive?.longOrNull ?: 0L,
            source = o["source"]?.jsonPrimitive?.contentOrNull,
            epubChapter = o["epub_chapter"]?.jsonPrimitive?.intOrNull,
            epubSentenceIndex = o["epub_sentence_index"]?.jsonPrimitive?.intOrNull,
            epubTextPreview = o["epub_text_preview"]?.jsonPrimitive?.contentOrNull,
            epubProgressPercent = o["epub_progress_percent"]?.jsonPrimitive?.floatOrNull,
            audioPositionMs = o["audio_position_ms"]?.jsonPrimitive?.intOrNull,
            hints = o["hints"]?.jsonArray?.map { h ->
                val ho = h.jsonObject
                PositionHint(
                    kind = ho["kind"]!!.jsonPrimitive.content,
                    deviceId = ho["device_id"]!!.jsonPrimitive.content,
                    value = ho["value"]!!.jsonPrimitive.content,
                    anchorRevision = ho["anchor_revision"]!!.jsonPrimitive.long,
                    audioPositionMs = ho["audio_position_ms"]?.jsonPrimitive?.intOrNull,
                )
            } ?: emptyList(),
        )
    }

    @Test
    fun any_anchor_always_yields_a_plan() {
        // Stated separately from the fixtures because the whole design rests
        // on it: a position holding an anchor must never resolve to "start of
        // book", which is what let chapter 0 be written over a real position.
        val anchored = listOf(
            StoredPosition(anchorRevision = 1, epubChapter = 3),
            StoredPosition(anchorRevision = 1, epubTextPreview = "althea counted the ships"),
            StoredPosition(anchorRevision = 1, epubProgressPercent = 12.5f),
            StoredPosition(anchorRevision = 1, audioPositionMs = 1000),
        )
        for (position in anchored) {
            val steps = planRestore(position, 40, "pixel", HINT_READIUM_LOCATOR)
            assertTrue("$position produced no restore plan", steps.isNotEmpty())
            assertTrue(hasAnchor(position))
        }
    }

    @Test
    fun reports_no_anchor_only_for_a_genuinely_empty_record() {
        assertFalse(hasAnchor(null))
        assertFalse(hasAnchor(StoredPosition(anchorRevision = 1)))
        assertFalse(hasAnchor(StoredPosition(anchorRevision = 1, epubProgressPercent = 0f)))
    }
}
