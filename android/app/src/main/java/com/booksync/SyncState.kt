package com.booksync

/**
 * Process-local state for passing sentence sync audio positions
 * directly from the ebook reader to the audio player, bypassing
 * the server round-trip which can race with position polling.
 */
object SyncState {
    /**
     * Audio position (ms) set by sentence-level sync in the reader.
     * The PlayerViewModel reads and clears this on init. If > 0,
     * the player seeks to this position instead of the server bookmark.
     */
    @Volatile
    var pendingAudioSeekMs: Long = -1L
}
