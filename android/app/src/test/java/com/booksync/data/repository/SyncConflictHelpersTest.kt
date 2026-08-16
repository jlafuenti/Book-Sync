package com.booksync.data.repository

import com.booksync.data.local.entity.BookmarkEntity
import com.booksync.data.local.entity.UserProgressEntity
import com.booksync.data.remote.PositionHintResponse
import com.booksync.data.remote.PositionResponse
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

    // ---------- PositionResponse.toBookmarkEntity ----------
    //
    // The precise Readium locator arrives as a position hint keyed by device,
    // not as the `epub_locator` column it used to share with every other
    // device (issue #102). Only a hint the server marks `current` is adopted:
    // a stale one describes a page the anchor has since moved away from.

    private fun locatorHint(
        value: String,
        audioMs: Int? = null,
        current: Boolean = true,
        deviceId: String = "pixel",
    ) = PositionHintResponse(
        kind = "readium_locator",
        device_id = deviceId,
        value = value,
        anchor_revision = 4,
        audio_position_ms = audioMs,
        current = current,
    )

    private fun position(
        source: String = "ebook",
        epubChapter: Int? = null,
        epubSentenceIndex: Int? = null,
        audioPositionMs: Int? = null,
        capturedAt: String? = null,
        deviceId: String? = null,
        deviceName: String? = null,
        hints: List<PositionHintResponse> = emptyList(),
        syncMapVersion: Int? = null,
    ) = PositionResponse(
        scope = "pair",
        book_pair_id = 42,
        source = source,
        anchor_revision = 4,
        epub_chapter = epubChapter,
        epub_sentence_index = epubSentenceIndex,
        sync_map_version = syncMapVersion,
        audio_position_ms = audioPositionMs,
        is_completed = false,
        captured_at = capturedAt,
        updated_at = "2026-04-12T15:30:00Z",
        device_id = deviceId,
        device_name = deviceName,
        hints = hints,
    )

    private fun previousBookmark(
        locator: String? = "{\"locator\":true}",
        locatorAudioMs: Int? = 999,
    ) = BookmarkEntity(
        bookPairId = 42,
        source = "ebook",
        epubChapter = 1,
        epubSentenceIndex = 1,
        audioPositionMs = 0,
        epubLocator = locator,
        locatorAudioMs = locatorAudioMs,
        updatedAt = "0",
        syncedToServer = true,
    )

    @Test
    fun `toBookmarkEntity carries the server's sync-map version onto the local row`() {
        // A pulled position is later pushed back (startup reconcile, offline
        // replay); the push must attest the version the server said the index
        // is expressed in, not whatever this device's cache happens to hold.
        val entity = position(epubSentenceIndex = 10, syncMapVersion = 6)
            .toBookmarkEntity(42, previousBookmark().copy(syncMapVersion = 2))

        assertEquals(6, entity.syncMapVersion)
    }

    @Test
    fun `toBookmarkEntity maps server fields and preserves previous locator state`() {
        val response = position(
            source = "audiobook",
            epubChapter = 3,
            epubSentenceIndex = 10,
            audioPositionMs = 123_456,
            capturedAt = "2026-04-12T15:29:00Z",
            deviceId = "device-b",
            deviceName = "Device B",
        )

        val entity = response.toBookmarkEntity(42, previousBookmark())

        assertEquals(42, entity.bookPairId)
        assertEquals("audiobook", entity.source)
        assertEquals(3, entity.epubChapter)
        assertEquals(10, entity.epubSentenceIndex)
        assertEquals(123_456, entity.audioPositionMs)
        // Server carried no current locator hint — the previous local locator
        // (and its audio anchor) survive.
        assertEquals("{\"locator\":true}", entity.epubLocator)
        assertEquals(999, entity.locatorAudioMs)
        assertEquals("2026-04-12T15:30:00Z", entity.updatedAt)
        assertEquals("2026-04-12T15:29:00Z", entity.capturedAt)
        assertEquals("device-b", entity.deviceId)
        assertEquals("Device B", entity.deviceName)
        assertTrue(entity.syncedToServer)
    }

    @Test
    fun `toBookmarkEntity keeps the local locator on an ebook write that carries none`() {
        // A web-written position carries a chapter anchor and no Readium
        // locator. Dropping the local locator here (as an earlier revision did,
        // mirroring a server rule that cleared it) leaves the reader with
        // nothing precise to restore from; combined with a restore path that
        // read "no locator" as "no position", that overwrote a real position
        // with chapter 0. The anchor moving is not a reason to destroy a hint.
        val response = position(epubChapter = 7, epubSentenceIndex = 2)

        val entity = response.toBookmarkEntity(42, previousBookmark("{\"local\":true}"))

        assertEquals("{\"local\":true}", entity.epubLocator)
        assertEquals(999, entity.locatorAudioMs)
        assertEquals(7, entity.epubChapter)
    }

    @Test
    fun `toBookmarkEntity takes the server locator hint and its audio anchor`() {
        // The hint's audio anchor round-trips (issue #40 step 4), so a *second*
        // device can judge whether the locator still describes where the audio
        // is — not just the device that wrote it.
        val response = position(
            hints = listOf(locatorHint("{\"fromServer\":true}", audioMs = 61_000)),
        )

        val entity = response.toBookmarkEntity(42, previousBookmark("{\"local\":true}"))

        assertEquals("{\"fromServer\":true}", entity.epubLocator)
        assertEquals(61_000, entity.locatorAudioMs)
    }

    @Test
    fun `toBookmarkEntity ignores a stale locator hint and keeps the local one`() {
        // `current = false` means the anchor moved after that hint was
        // captured. It is deliberately not deleted server-side — its own
        // device makes it current again by re-capturing — but adopting it here
        // would restore to a page the reader has since left.
        val response = position(
            epubChapter = 9,
            hints = listOf(locatorHint("{\"stale\":true}", audioMs = 1, current = false)),
        )

        val entity = response.toBookmarkEntity(42, previousBookmark("{\"local\":true}"))

        assertEquals("{\"local\":true}", entity.epubLocator)
        assertEquals(999, entity.locatorAudioMs)
    }

    @Test
    fun `toBookmarkEntity with no previous entity leaves locator null`() {
        val entity = position().toBookmarkEntity(42, null)

        assertNull(entity.epubLocator)
        assertNull(entity.locatorAudioMs)
    }

    @Test
    fun `toBookmarkEntity falls back to the request pair id for a standalone scope`() {
        // A standalone-scoped response carries a null book_pair_id, but the
        // local bookmark table is keyed by pair.
        val response = position().copy(scope = "ebook", book_pair_id = null, ebook_id = 7)

        assertEquals(42, response.toBookmarkEntity(42, null).bookPairId)
    }

    // ---------- PositionResponse.toProgressEntity ----------

    @Test
    fun `toProgressEntity uses request context for mediaType and mediaId and prefers captured_at`() {
        val response = position(
            source = "audiobook",
            audioPositionMs = 55_000,
            capturedAt = "2026-04-12T15:29:00Z",
            deviceId = "device-b",
            deviceName = "Device B",
        )

        val entity = response.toProgressEntity("audiobook", 99)

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
    fun `toProgressEntity falls back to updated_at when captured_at is absent`() {
        val entity = position().toProgressEntity("ebook", 7)

        assertEquals(
            java.time.Instant.parse("2026-04-12T15:30:00Z").toEpochMilli(),
            entity.updatedAt
        )
    }

    @Test
    fun `toProgressEntity keeps the web reader CFI, which this device cannot produce`() {
        // epub_cfi is the web reader's hint. Android can neither produce nor
        // judge it, so an Android-side mapping must never blank it — it used
        // to arrive as the `user_progress.epub_cfi` mirror column, which no
        // longer exists (issue #102).
        val previous = UserProgressEntity(
            mediaType = "ebook",
            mediaId = 7,
            bookPairId = 42,
            epubCfi = "epubcfi(/6/4!/4/2)",
            epubChapter = 1,
            epubProgressPercent = 5f,
            audioPositionMs = null,
            isCompleted = false,
            updatedAt = 0L,
            deviceId = null,
        )

        val entity = position(epubChapter = 9).toProgressEntity("ebook", 7, previous)

        assertEquals("epubcfi(/6/4!/4/2)", entity.epubCfi)
        assertEquals(9, entity.epubChapter)
    }
}
