package com.booksync.ui.player

import com.booksync.data.local.entity.AudioBookEntity
import com.booksync.player.MediaId
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Regression test for issue #141: the phone player loaded standalone audiobooks
 * under "standalone_N" while AudioPlayerService builds the same books as
 * "audiobook_N" — so the service's heartbeat, pause flush, STATE_ENDED
 * completion and Cast paths all silently no-oped for standalone playback.
 *
 * Both sites must build the id the same way for the same entity.
 */
class StandaloneMediaIdParityTest {

    private val audio = AudioBookEntity(
        id = 17,
        title = "A Standalone Audiobook",
        author = "Author",
        filename = "book.m4b",
        durationSeconds = 3600,
        format = "m4b",
        series = null,
        seriesIndex = null,
        uploadedAt = "2026-01-01T00:00:00",
        isDownloaded = true,
    )

    @Test
    fun `the phone player builds the id the service dispatchers recognise`() {
        // standaloneMediaId is the id-building expression from
        // PlayerViewModel.loadStandaloneAudio; the service's
        // buildAudiobookMediaItem uses MediaId.Audiobook for the same entity.
        assertEquals(MediaId.Audiobook(audio.id).value, standaloneMediaId(audio))
    }

    @Test
    fun `the id parses back to the audiobook, so every dispatcher can route it`() {
        assertEquals(MediaId.Audiobook(17), MediaId.parse(standaloneMediaId(audio)))
    }
}
