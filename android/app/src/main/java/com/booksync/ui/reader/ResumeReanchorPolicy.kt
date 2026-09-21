package com.booksync.ui.reader

import com.booksync.data.sync.LOCATOR_REUSE_THRESHOLD_MS
import kotlin.math.abs

/**
 * Decides whether a reader coming back to the foreground (the `onStart` that
 * follows an `onStop`) needs to re-run the restore ladder before accepting
 * any more saves — issue #682.
 *
 * A reader left open never re-restores on its own: [ReaderActivity
 * .getInitialLocator] runs the ladder once, at open, and nothing about that
 * changes just because the activity went to the background. If the
 * audiobook was played from elsewhere in the meantime — Android Auto, the
 * notification, another device — the page on screen is stale, and the
 * reader's own autosave would happily write it back over the real, more
 * recent listening position with `source = ebook` (contract § "The restore
 * ladder" and § "The write gate" both assume the ladder ran recently, which
 * a reader left open breaks).
 *
 * The decision is audio movement, not a timestamp: device clocks can be
 * wrong or skewed, but an audio position only advances when something
 * actually played. [LOCATOR_REUSE_THRESHOLD_MS] is reused verbatim — it is
 * the same "close enough to still be this page" distance the restore
 * ladder's own hint-staleness check already uses
 * (`PositionResolver.usableHint`), so a reader that comes back and a reader
 * that opens fresh apply the same notion of "moved on".
 */
object ResumeReanchorPolicy {

    /**
     * @param source the canonical record's `source` from a fresh fetch made
     *   on this `onStart` — not the value the reader had at open.
     * @param recordAudioMs that same fresh fetch's `audio_position_ms`.
     * @param baselineAudioMs the audio position the reader last knew the
     *   record to hold — the record's audio at open, kept current as the
     *   reader's own saves move it. Null when nothing has established one
     *   yet (a fresh open with no prior audio position at all).
     */
    fun shouldReanchor(source: String?, recordAudioMs: Int?, baselineAudioMs: Int?): Boolean {
        if (source != "audiobook") return false
        if (recordAudioMs == null) return false
        if (baselineAudioMs == null) return true
        return abs(recordAudioMs - baselineAudioMs) >= LOCATOR_REUSE_THRESHOLD_MS
    }
}
