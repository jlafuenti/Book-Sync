package com.booksync.player

/**
 * The two playback offsets, in one place (issue #42).
 *
 * Before this existed the same gesture moved a different distance depending on
 * where you made it: 15s on the phone player, 10s in Android Auto and on the
 * notification, 15s back / 30s forward on the web, and a 2s jump on the reader's
 * text→audio handoff against a stated server default of 10s.
 *
 * There is no cross-platform config channel for these, so each platform keeps its
 * own copy and they are kept equal by hand:
 *
 *  - Android  — this object
 *  - Web      — `SKIP_SECONDS` / `RESUME_REWIND_SECONDS` in `AudioPlayerContext.jsx`
 *  - Server   — `default_rewind_seconds` in `config.py`
 *
 * See `docs/position-sync-contract.md` § Playback offsets before changing either
 * value; `PlaybackOffsetsTest` asserts them literally so a one-sided edit fails CI.
 *
 * The seek helpers are pure so they can be unit-tested away from a live player —
 * the same reason [ContinuousPlaybackLog] is a seeded object rather than a bare
 * timestamp inside the service.
 */
object PlaybackOffsets {

    /** Skip back and skip forward, on every surface. */
    const val SKIP_MS = 30_000L

    /**
     * How far a resume backs up, so playback doesn't pick up mid-word. Also the
     * size of the reader's text→audio handoff jump, which lands you just before
     * the sentence you were reading rather than on top of it.
     */
    const val RESUME_REWIND_MS = 5_000L

    /** Where a resume should land, given where playback was paused. */
    fun resumePosition(currentMs: Long): Long =
        (currentMs - RESUME_REWIND_MS).coerceAtLeast(0L)

    fun skipBackPosition(currentMs: Long): Long =
        (currentMs - SKIP_MS).coerceAtLeast(0L)

    /**
     * [durationMs] is ignored when it is unknown — ExoPlayer reports
     * `C.TIME_UNSET` (a large *negative*) until the media is prepared, and
     * coercing against that would drag the position backwards instead of forwards.
     */
    fun skipForwardPosition(currentMs: Long, durationMs: Long): Long {
        val target = currentMs + SKIP_MS
        return if (durationMs > 0L) target.coerceAtMost(durationMs) else target
    }
}
