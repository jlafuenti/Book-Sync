package com.booksync.player

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * The two playback offsets (issue #42).
 *
 * The bug these pin: the same button used to move a different distance on every
 * surface — 15s on the phone player, 10s in Android Auto, 15s back / 30s forward
 * on the web, and a 2s jump on the reader's text→audio handoff against a stated
 * server default of 10s. The values are asserted literally here so a change on
 * one platform can't quietly desync from the others; the matching web copy lives
 * in `AudioPlayerContext.jsx` and the server's in `config.default_rewind_seconds`.
 */
class PlaybackOffsetsTest {

    @Test
    fun `skip is 30 seconds and resume rewind is 5`() {
        assertEquals(30_000L, PlaybackOffsets.SKIP_MS)
        assertEquals(5_000L, PlaybackOffsets.RESUME_REWIND_MS)
    }

    @Test
    fun `resume backs up by the rewind offset`() {
        assertEquals(85_000L, PlaybackOffsets.resumePosition(90_000L))
    }

    @Test
    fun `resume clamps at the start of the file`() {
        assertEquals(0L, PlaybackOffsets.resumePosition(2_000L))
        assertEquals(0L, PlaybackOffsets.resumePosition(0L))
    }

    @Test
    fun `skip back moves one skip offset`() {
        assertEquals(70_000L, PlaybackOffsets.skipBackPosition(100_000L))
    }

    @Test
    fun `skip back clamps at the start of the file`() {
        assertEquals(0L, PlaybackOffsets.skipBackPosition(10_000L))
    }

    @Test
    fun `skip forward moves one skip offset`() {
        assertEquals(130_000L, PlaybackOffsets.skipForwardPosition(100_000L, durationMs = 600_000L))
    }

    @Test
    fun `skip forward clamps at the end of the file`() {
        assertEquals(600_000L, PlaybackOffsets.skipForwardPosition(590_000L, durationMs = 600_000L))
    }

    @Test
    fun `skip forward ignores an unknown duration rather than seeking backwards`() {
        // ExoPlayer reports C.TIME_UNSET (a large negative) before the media is
        // prepared. Coercing against that would drag the position backwards.
        assertEquals(130_000L, PlaybackOffsets.skipForwardPosition(100_000L, durationMs = -9_223_372_036_854_775_807L))
    }
}
