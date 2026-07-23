package com.booksync.data.repository

import com.booksync.data.local.entity.BookmarkLogEntity
import com.booksync.data.remote.BookmarkLogResponse
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter

/**
 * Regression tests for a bug found while verifying issue #54's Android History-tab
 * device attribution in production: `BookmarkLogEntity` never carried device_id/
 * device_name, so every entry read from the local cache (which `getBookmarkHistory`
 * always reads from, even right after a successful server fetch) silently lost device
 * attribution regardless of what the server actually returned. Also covers the
 * optimistic local-only history entry's timestamp format, which previously used
 * `Instant.toString()`'s trailing "Z" — a shape `formatAbsoluteTime` cannot parse.
 */
class BookmarkLogDeviceAttributionTest {

    @Test
    fun `BookmarkLogResponse toEntity carries device_id and device_name into the entity`() {
        val response = BookmarkLogResponse(
            id = 1,
            source = "audiobook",
            prev_epub_chapter = null,
            prev_epub_sentence_index = null,
            prev_audio_position_ms = 1000,
            new_epub_chapter = null,
            new_epub_sentence_index = null,
            new_audio_position_ms = 2000,
            changed_at = "2026-07-23T19:18:11.345658",
            device_id = "device-abc",
            device_name = "Jesse's Pixel",
        )

        val entity = response.toEntity(pairId = 84)

        assertEquals("device-abc", entity.deviceId)
        assertEquals("Jesse's Pixel", entity.deviceName)
    }

    @Test
    fun `BookmarkLogEntity toResponse carries deviceId and deviceName into the response`() {
        val entity = BookmarkLogEntity(
            serverId = 1,
            bookPairId = 84,
            source = "audiobook",
            prevEpubChapter = null,
            prevEpubSentenceIndex = null,
            prevAudioPositionMs = 1000,
            newEpubChapter = null,
            newEpubSentenceIndex = null,
            newAudioPositionMs = 2000,
            changedAt = "2026-07-23T19:18:11.345658",
            deviceId = "device-abc",
            deviceName = "Jesse's Pixel",
        )

        val response = entity.toResponse()

        assertEquals("device-abc", response.device_id)
        assertEquals("Jesse's Pixel", response.device_name)
    }

    @Test
    fun `localHistoryTimestamp produces a format parseable the same way server timestamps are`() {
        // Server sends timestamps like "2026-03-04T21:52:14.923182" — 'T'-separated,
        // no trailing zone suffix (see PlayerScreen.kt's formatAbsoluteTime doc comment).
        // The optimistic local-only history entry must match this shape, not
        // java.time.Instant.toString()'s trailing "Z" (which the parser rejects).
        val epochMillis = 1784924171345L // arbitrary fixed instant

        val result = localHistoryTimestamp(epochMillis)

        assertFalse(result.endsWith("Z"))
        // Must be parseable by the exact pattern formatAbsoluteTime uses.
        val formatter = DateTimeFormatter.ofPattern(
            "yyyy-MM-dd'T'HH:mm:ss[.SSSSSS][.SSSSS][.SSSS][.SSS][.SS][.S]"
        )
        LocalDateTime.parse(result, formatter) // throws if unparseable
    }
}
