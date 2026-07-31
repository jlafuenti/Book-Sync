package com.booksync.data.repository

import com.booksync.data.local.entity.BookmarkEntity
import com.booksync.data.remote.BookmarkResponse
import com.booksync.data.remote.ProgressResponse
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Unit tests for the pure timestamp/mapping helpers extracted from [BookSyncRepository] as
 * part of issue #54's multi-device conflict resolution. These are top-level `internal`
 * functions (not repository members) specifically so they're testable without constructing
 * a BookSyncRepository — which would otherwise require faking every DAO, the API, and
 * DeviceIdManager (no mocking framework is available in this project).
 *
 * `processPendingSync`'s 409-drop behavior itself (the actual bug fix) is NOT covered here —
 * it requires a mocked Retrofit Response and is documented as an untestable gap in the task
 * report; these tests instead pin the pure logic that behavior depends on.
 */
class SyncConflictHelpersTest {

    // ---------- parseSyncTimestamp ----------

    @Test
    fun `parseSyncTimestamp reads epoch millis string`() {
        assertEquals(1712880000000L, parseSyncTimestamp("1712880000000"))
    }

    @Test
    fun `parseSyncTimestamp reads ISO-8601 with Z suffix`() {
        assertEquals(
            java.time.Instant.parse("2026-04-12T15:30:00Z").toEpochMilli(),
            parseSyncTimestamp("2026-04-12T15:30:00Z")
        )
    }

    @Test
    fun `parseSyncTimestamp reads ISO-8601 without zone as UTC`() {
        val expected = java.time.LocalDateTime.parse("2026-04-12T15:30:00")
            .atZone(java.time.ZoneOffset.UTC)
            .toInstant()
            .toEpochMilli()
        assertEquals(expected, parseSyncTimestamp("2026-04-12T15:30:00"))
    }

    @Test
    fun `parseSyncTimestamp returns 0 for null blank or garbage`() {
        assertEquals(0L, parseSyncTimestamp(null))
        assertEquals(0L, parseSyncTimestamp(""))
        assertEquals(0L, parseSyncTimestamp("   "))
        assertEquals(0L, parseSyncTimestamp("not-a-timestamp"))
    }

    // ---------- capturedAtIsoFromMillis / toCapturedAtIso ----------

    @Test
    fun `capturedAtIsoFromMillis produces a Z-suffixed ISO-8601 instant`() {
        val iso = capturedAtIsoFromMillis(1712880000000L)
        assertTrue(iso.endsWith("Z"))
        assertEquals(1712880000000L, java.time.Instant.parse(iso).toEpochMilli())
    }

    @Test
    fun `toCapturedAtIso round-trips a local epoch-millis string`() {
        val iso = toCapturedAtIso("1712880000000")
        assertEquals(1712880000000L, java.time.Instant.parse(iso!!).toEpochMilli())
    }

    @Test
    fun `toCapturedAtIso round-trips an already-ISO string`() {
        val iso = toCapturedAtIso("2026-04-12T15:30:00Z")
        assertEquals(
            java.time.Instant.parse("2026-04-12T15:30:00Z").toEpochMilli(),
            java.time.Instant.parse(iso!!).toEpochMilli()
        )
    }

    @Test
    fun `toCapturedAtIso returns null for null blank or unparseable so callers omit captured_at`() {
        assertNull(toCapturedAtIso(null))
        assertNull(toCapturedAtIso(""))
        assertNull(toCapturedAtIso("garbage"))
    }

    // ---------- preferCapturedAt ----------

    @Test
    fun `preferCapturedAt prefers a non-blank captured_at over updated_at`() {
        assertEquals("2026-04-12T15:30:00Z", preferCapturedAt("2026-04-12T15:30:00Z", "1700000000000"))
    }

    @Test
    fun `preferCapturedAt falls back to updated_at when captured_at is null or blank`() {
        assertEquals("1700000000000", preferCapturedAt(null, "1700000000000"))
        assertEquals("1700000000000", preferCapturedAt("", "1700000000000"))
        assertEquals("1700000000000", preferCapturedAt("   ", "1700000000000"))
    }

    // ---------- BookmarkResponse.toEntity ----------

    @Test
    fun `BookmarkResponse toEntity maps server fields and preserves previous locator state`() {
        val response = BookmarkResponse(
            id = 1,
            user_id = 2,
            book_pair_id = 42,
            source = "audiobook",
            epub_chapter = 3,
            epub_sentence_index = 10,
            audio_position_ms = 123_456,
            epub_locator = null,
            updated_at = "2026-04-12T15:30:00Z",
            synced_at = null,
            device_id = "device-b",
            device_name = "Device B",
            captured_at = "2026-04-12T15:29:00Z",
        )
        val previous = BookmarkEntity(
            bookPairId = 42,
            source = "ebook",
            epubChapter = 1,
            epubSentenceIndex = 1,
            audioPositionMs = 0,
            epubLocator = "{\"locator\":true}",
            locatorAudioMs = 999,
            updatedAt = "0",
            syncedToServer = true,
        )

        val entity = response.toEntity(previous)

        assertEquals(42, entity.bookPairId)
        assertEquals("audiobook", entity.source)
        assertEquals(3, entity.epubChapter)
        assertEquals(10, entity.epubSentenceIndex)
        assertEquals(123_456, entity.audioPositionMs)
        // Server sent no locator — previous local locator (and its audio anchor) survive.
        assertEquals("{\"locator\":true}", entity.epubLocator)
        assertEquals(999, entity.locatorAudioMs)
        assertEquals("2026-04-12T15:30:00Z", entity.updatedAt)
        assertEquals("2026-04-12T15:29:00Z", entity.capturedAt)
        assertEquals("device-b", entity.deviceId)
        assertEquals("Device B", entity.deviceName)
        assertTrue(entity.syncedToServer)
    }

    @Test
    fun `BookmarkResponse toEntity keeps the local locator on an ebook write that carries none`() {
        // A web-written bookmark carries a chapter anchor and no Readium
        // locator. Dropping the local locator here (as an earlier revision did,
        // mirroring a server rule that cleared it) leaves the reader with
        // nothing precise to restore from; combined with a restore path that
        // read "no locator" as "no position", that overwrote a real position
        // with chapter 0. The anchor moving is not a reason to destroy a hint.
        val response = BookmarkResponse(
            id = 1,
            user_id = 2,
            book_pair_id = 42,
            source = "ebook",
            epub_chapter = 7,
            epub_sentence_index = 2,
            epub_locator = null,
            updated_at = "2026-04-12T15:30:00Z",
        )
        val previous = BookmarkEntity(
            bookPairId = 42,
            source = "ebook",
            epubChapter = 1,
            epubSentenceIndex = 1,
            audioPositionMs = 0,
            epubLocator = "{\"local\":true}",
            locatorAudioMs = 999,
            updatedAt = "0",
        )

        val entity = response.toEntity(previous)

        assertEquals("{\"local\":true}", entity.epubLocator)
        assertEquals(999, entity.locatorAudioMs)
        assertEquals(7, entity.epubChapter)
    }

    @Test
    fun `BookmarkResponse toEntity takes the server locator and its audio anchor`() {
        // locator_audio_ms now round-trips through the server (issue #40 step
        // 4), so a *second* device can judge whether the locator still
        // describes where the audio is — not just the device that wrote it.
        val response = BookmarkResponse(
            id = 1,
            user_id = 2,
            book_pair_id = 42,
            source = "ebook",
            epub_locator = "{\"fromServer\":true}",
            locator_audio_ms = 61_000,
            updated_at = "2026-04-12T15:30:00Z",
        )
        val previous = BookmarkEntity(
            bookPairId = 42,
            source = "ebook",
            epubChapter = 1,
            epubSentenceIndex = 1,
            audioPositionMs = 0,
            epubLocator = "{\"local\":true}",
            locatorAudioMs = 999,
            updatedAt = "0",
        )

        val entity = response.toEntity(previous)

        assertEquals("{\"fromServer\":true}", entity.epubLocator)
        assertEquals(61_000, entity.locatorAudioMs)
    }

    @Test
    fun `BookmarkResponse toEntity with no previous entity leaves locator null`() {
        val response = BookmarkResponse(
            id = 1,
            user_id = 2,
            book_pair_id = 42,
            source = "ebook",
            updated_at = "2026-04-12T15:30:00Z",
        )

        val entity = response.toEntity(null)

        assertNull(entity.epubLocator)
        assertNull(entity.locatorAudioMs)
    }

    // ---------- ProgressResponse.toEntity ----------

    @Test
    fun `ProgressResponse toEntity uses request context for mediaType and mediaId and prefers captured_at`() {
        val response = ProgressResponse(
            id = 5,
            user_id = 2,
            media_type = "audiobook",
            book_pair_id = 42,
            audiobook_id = 99,
            audio_position_ms = 55_000,
            is_completed = false,
            updated_at = "2026-04-12T15:30:00Z",
            device_id = "device-b",
            device_name = "Device B",
            captured_at = "2026-04-12T15:29:00Z",
        )

        val entity = response.toEntity("audiobook", 99)

        assertEquals("audiobook", entity.mediaType)
        assertEquals(99, entity.mediaId)
        assertEquals(42, entity.bookPairId)
        assertEquals(55_000, entity.audioPositionMs)
        assertEquals("device-b", entity.deviceId)
        assertEquals("Device B", entity.deviceName)
        assertEquals("2026-04-12T15:29:00Z", entity.capturedAt)
        // Comparison timestamp is derived from captured_at (the true capture moment), not
        // updated_at, per the issue #54 contract.
        assertEquals(
            java.time.Instant.parse("2026-04-12T15:29:00Z").toEpochMilli(),
            entity.updatedAt
        )
        assertTrue(entity.syncedToServer)
    }

    @Test
    fun `ProgressResponse toEntity falls back to updated_at when captured_at is absent`() {
        val response = ProgressResponse(
            id = 5,
            user_id = 2,
            media_type = "ebook",
            ebook_id = 7,
            is_completed = false,
            updated_at = "2026-04-12T15:30:00Z",
        )

        val entity = response.toEntity("ebook", 7)

        assertEquals(
            java.time.Instant.parse("2026-04-12T15:30:00Z").toEpochMilli(),
            entity.updatedAt
        )
    }
}
