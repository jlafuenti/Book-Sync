package com.booksync.data.repository

/**
 * Android's local mirror of the server's completion crossing rule
 * (`server/services/position_service.py::_auto_complete`,
 * `docs/position-sync-contract.md` § Completion, issues #56 and #584).
 *
 * The server is authoritative — it re-derives `is_completed` from scratch on
 * every write and that is what every client ultimately converges on. This
 * object exists so a *local* save can apply the same "leaving the end zone
 * un-finishes the book" transition to the cached `user_progress` row before
 * the server round trip completes, so a book reappears on Continue Listening
 * immediately and offline (issue #584) rather than only after the next sync.
 *
 * The two thresholds mirror `server/config.py`'s `auto_complete_epub_percent`
 * (98.0) and `auto_complete_audio_tail_seconds` (120s). Neither setting is
 * exposed over the API — both are plain environment-backed `Settings` fields,
 * not part of the DB-backed `system_settings` table the System page edits —
 * so there is no remote-config channel for Android to fetch them from (unlike,
 * say, the transcription provider). The values are duplicated here by hand,
 * the same shape as [com.booksync.player.PlaybackOffsets]; keep them equal to
 * `server/config.py` if either one ever changes.
 */
object AutoComplete {

    /** Mirrors `auto_complete_epub_percent` in `server/config.py`. */
    const val EPUB_END_ZONE_PERCENT = 98.0f

    /** Mirrors `auto_complete_audio_tail_seconds` in `server/config.py`. */
    const val AUDIO_TAIL_MS = 120_000L

    fun inEpubEndZone(percent: Float?): Boolean =
        percent != null && percent >= EPUB_END_ZONE_PERCENT

    fun inAudioEndZone(positionMs: Int?, durationMs: Long?): Boolean =
        positionMs != null && durationMs != null && durationMs - positionMs <= AUDIO_TAIL_MS
}
