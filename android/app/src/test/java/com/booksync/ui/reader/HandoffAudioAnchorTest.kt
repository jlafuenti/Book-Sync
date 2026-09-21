package com.booksync.ui.reader

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * [HandoffAudioAnchor] is the "Switch to Reader" handoff's one-shot audio
 * value (issue #682 follow-up). [EXTRA_HANDOFF_AUDIO_MS] describes where the
 * audiobook was WHEN THE HANDOFF HAPPENED — meaningful only for the very
 * first restore [getInitialLocator] runs at open.
 *
 * [reanchorAfterResume] calls [ReaderActivity.getInitialLocator] again later,
 * after fetching the record fresh, specifically because listening may have
 * moved on since. Before this fix, that second call re-read the same intent
 * extra and prepended the ORIGINAL handoff position ahead of the freshly
 * fetched record every time — landing a resume-after-listening back at the
 * spot where the reader was first opened, rather than where listening
 * actually stopped. That is the exact failure #682 reported, reintroduced
 * through a second path.
 *
 * [consume] is what fixes it: the value is readable exactly once.
 */
class HandoffAudioAnchorTest {

    @Test
    fun `the first consume returns the handoff value`() {
        val handoff = HandoffAudioAnchor(16_355_289)
        assertEquals(16_355_289, handoff.consume())
    }

    @Test
    fun `a second consume returns zero, not the original value again`() {
        val handoff = HandoffAudioAnchor(16_355_289)
        handoff.consume()
        assertEquals(0, handoff.consume())
    }

    @Test
    fun `every consume after the first returns zero`() {
        val handoff = HandoffAudioAnchor(16_355_289)
        handoff.consume()
        handoff.consume()
        assertEquals(0, handoff.consume())
    }

    @Test
    fun `an ordinary open with no handoff consumes as zero from the start`() {
        // withHandoffAnchor treats 0 as "no handoff" and is a no-op — this is
        // the shape every ordinary (non-handoff) reader open has.
        val handoff = HandoffAudioAnchor(0)
        assertEquals(0, handoff.consume())
        assertEquals(0, handoff.consume())
    }
}
