package com.booksync.ui.reader

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * [ResumeReanchorPolicy.shouldReanchor] pure-logic tests (issue #682).
 *
 * A reader left open never re-restores on its own: the ladder in
 * [ReaderActivity.getInitialLocator] runs once, at open, and going to the
 * background and back does nothing position-related by itself. If the
 * audiobook was played from elsewhere in the meantime — Android Auto, the
 * notification, another device — the page on screen is stale, and the
 * reader's own autosave would happily write it back over the real, more
 * recent listening position. This policy is what [ReaderActivity] consults on
 * the next `onStart` after an `onStop` to decide whether that has happened.
 */
class ResumeReanchorPolicyTest {

    @Test
    fun `audiobook source with audio far from baseline re-anchors`() {
        assertTrue(
            ResumeReanchorPolicy.shouldReanchor(
                source = "audiobook", recordAudioMs = 900_000, baselineAudioMs = 100_000,
            ),
        )
    }

    @Test
    fun `ebook source never re-anchors no matter how far the audio moved`() {
        assertFalse(
            ResumeReanchorPolicy.shouldReanchor(
                source = "ebook", recordAudioMs = 900_000, baselineAudioMs = 100_000,
            ),
        )
    }

    @Test
    fun `audio within the reuse threshold does not re-anchor`() {
        assertFalse(
            ResumeReanchorPolicy.shouldReanchor(
                source = "audiobook", recordAudioMs = 105_000, baselineAudioMs = 100_000,
            ),
        )
    }

    @Test
    fun `a null baseline with an audiobook position re-anchors`() {
        // Nothing has established a baseline yet (a fresh open with no prior
        // audio, or a save that never resolved one) — freshness against
        // "nothing" cannot be proven, so this defaults to re-anchoring rather
        // than trusting a page that might be hours stale.
        assertTrue(
            ResumeReanchorPolicy.shouldReanchor(
                source = "audiobook", recordAudioMs = 42_000, baselineAudioMs = null,
            ),
        )
    }

    @Test
    fun `no audio position on the record at all never re-anchors`() {
        // Nothing to re-anchor TO — the ladder's own audio rung would have
        // nowhere to go either.
        assertFalse(
            ResumeReanchorPolicy.shouldReanchor(
                source = "audiobook", recordAudioMs = null, baselineAudioMs = 100_000,
            ),
        )
    }

    @Test
    fun `movement exactly at the threshold re-anchors`() {
        assertTrue(
            ResumeReanchorPolicy.shouldReanchor(
                source = "audiobook", recordAudioMs = 130_000, baselineAudioMs = 100_000,
            ),
        )
    }
}
