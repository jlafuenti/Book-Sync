package com.booksync.data.repository

import org.junit.Assert.assertEquals
import org.junit.Test

/** Pins [AutoComplete]'s thresholds and zone checks against the server's
 *  `_in_epub_end_zone` / `_in_audio_end_zone` (issue #584). */
class AutoCompleteTest {

    @Test
    fun `epub end zone is at or above the threshold, unset never counts`() {
        assertEquals(false, AutoComplete.inEpubEndZone(null))
        assertEquals(false, AutoComplete.inEpubEndZone(97.99f))
        assertEquals(true, AutoComplete.inEpubEndZone(98.0f))
        assertEquals(true, AutoComplete.inEpubEndZone(99.5f))
    }

    @Test
    fun `audio end zone is within the tail, unknown position or duration never counts`() {
        val duration = 3_600_000L // 1 hour

        assertEquals(false, AutoComplete.inAudioEndZone(null, duration))
        assertEquals(false, AutoComplete.inAudioEndZone(1_000, null))
        // 121s from the end: outside.
        assertEquals(false, AutoComplete.inAudioEndZone(3_479_000, duration))
        // Exactly 120s from the end: inside (the boundary itself counts, same
        // as the server's `<=`).
        assertEquals(true, AutoComplete.inAudioEndZone(3_480_000, duration))
        // 10s from the end: inside.
        assertEquals(true, AutoComplete.inAudioEndZone(3_590_000, duration))
    }
}
