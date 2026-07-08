package com.booksync.sync

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Cross-platform parity: SyncMatcher.normalizeForSearch must produce exactly the
 * same output as the server's _normalize_for_search for every shared golden vector.
 * Fixtures come from server/tests/fixtures/sync_parity/ (copied onto the test
 * classpath by the copySyncParityFixtures Gradle task).
 */
class SyncMatcherParityTest {

    @Test
    fun normalizeForSearch_matches_server_golden_vectors() {
        val stream = javaClass.classLoader
            ?.getResourceAsStream("sync_parity/normalize_cases.json")
            ?: error("normalize_cases.json not on the test classpath — the copySyncParityFixtures task must run")

        val text = stream.bufferedReader(Charsets.UTF_8).use { it.readText() }
        val cases = Json.parseToJsonElement(text).jsonArray
        assertTrue("expected at least one golden vector", cases.size > 0)

        for (case in cases) {
            val obj = case.jsonObject
            val input = obj["input"]!!.jsonPrimitive.content
            val expected = obj["expected"]!!.jsonPrimitive.content
            assertEquals(
                "normalizeForSearch mismatch for input=<${input.take(40)}>",
                expected,
                SyncMatcher.normalizeForSearch(input),
            )
        }
    }
}
