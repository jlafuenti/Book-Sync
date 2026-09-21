package com.booksync.ui.reader

/**
 * The "Switch to Reader" handoff's audio position — [ReaderActivity
 * .EXTRA_HANDOFF_AUDIO_MS] — as a one-shot value (issue #682 follow-up).
 *
 * The handoff describes where the audiobook was at the moment the player
 * handed off to the reader, so it is only meaningful for the very first
 * restore [ReaderActivity.getInitialLocator] runs at open — that is what
 * [withHandoffAnchor] prepends it for. [ReaderActivity.reanchorAfterResume]
 * calls `getInitialLocator` again later, deliberately, because listening may
 * have moved on since — it fetches the record fresh specifically to see
 * that. Reading the same intent extra a second time would re-prepend the
 * ORIGINAL handoff position ahead of that freshly fetched record every time,
 * landing a resume-after-listening back at the spot the reader was first
 * opened at rather than where listening actually stopped: the exact failure
 * #682 reported, reintroduced through this second path.
 *
 * [consume] is what prevents that: it returns [initialValueMs] once, and 0
 * — "no handoff" per [withHandoffAnchor] — on every call after.
 */
class HandoffAudioAnchor(initialValueMs: Int) {
    private var remaining = initialValueMs

    fun consume(): Int {
        val value = remaining
        remaining = 0
        return value
    }
}
