package com.booksync.ui.player

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Issue #171: the player gated play/pause, chapter skip, skip back/forward,
 * speed and the sleep timer on `isDownloaded`, so a book that was not on the
 * device showed a row of dead controls under a small warning — the screen
 * looked broken rather than "download this first".
 *
 * Streaming makes "downloaded" the wrong question everywhere except Cast,
 * which serves the *phone's* copy over the LAN and therefore genuinely needs
 * the file. Compose screens are not unit-testable in this module, so the rule
 * lives here.
 */
class PlayerTransportStateTest {

    @Test
    fun `not downloaded but online streams`() {
        assertTrue(transportEnabled(isDownloaded = false, isOnline = true, isCasting = false))
    }

    @Test
    fun `not downloaded and offline has nothing to play`() {
        assertFalse(transportEnabled(isDownloaded = false, isOnline = false, isCasting = false))
    }

    @Test
    fun `downloaded plays online or off`() {
        assertTrue(transportEnabled(isDownloaded = true, isOnline = true, isCasting = false))
        assertTrue(transportEnabled(isDownloaded = true, isOnline = false, isCasting = false))
    }

    @Test
    fun `casting without the file on the phone is disabled`() {
        // LocalCastHttpServer streams the downloaded file from the phone to the
        // receiver; there is no server-side path for Cast, so a streaming book
        // cannot be cast at all.
        assertFalse(transportEnabled(isDownloaded = false, isOnline = true, isCasting = true))
        assertFalse(transportEnabled(isDownloaded = false, isOnline = false, isCasting = true))
    }

    @Test
    fun `casting a downloaded book is enabled`() {
        assertTrue(transportEnabled(isDownloaded = true, isOnline = false, isCasting = true))
    }

    @Test
    fun `casting is offered only for a downloaded book`() {
        assertTrue(castAvailable(isDownloaded = true))
        assertFalse(castAvailable(isDownloaded = false))
    }

    // --- what the screen says instead of "Audiobook not downloaded." -------

    @Test
    fun `a streaming book is described as streaming, not as broken`() {
        assertEquals(
            "Streaming from your server. Download for offline listening and casting.",
            downloadHintMessage(isDownloaded = false, isOnline = true),
        )
    }

    @Test
    fun `an offline undownloaded book says what is actually wrong`() {
        assertEquals(
            "Offline — download this audiobook to listen without a connection.",
            downloadHintMessage(isDownloaded = false, isOnline = false),
        )
    }

    @Test
    fun `a downloaded book has nothing to say`() {
        assertEquals(null, downloadHintMessage(isDownloaded = true, isOnline = true))
        assertEquals(null, downloadHintMessage(isDownloaded = true, isOnline = false))
    }

    @Test
    fun `neither hint blames the user for a broken player`() {
        // The old copy was "Audiobook not downloaded." next to eight dead
        // buttons, which reads as a bug rather than as an offer.
        listOf(
            downloadHintMessage(isDownloaded = false, isOnline = true)!!,
            downloadHintMessage(isDownloaded = false, isOnline = false)!!,
        ).forEach { assertFalse(it == "Audiobook not downloaded.") }
    }
}
