package com.booksync.ui.player

/**
 * Pure rules for what the player screen may offer, extracted so they can be
 * tested — Compose screens are not unit-testable in this module (no emulator,
 * no Robolectric), and `com.booksync.ui.*Screen*` is excluded from coverage
 * for exactly that reason.
 *
 * Issue #171: play/pause, the scrubber, chapter skip, skip back/forward, speed
 * and the sleep timer were all gated on `isDownloaded`, so an undownloaded book
 * showed a row of dead controls under "Audiobook not downloaded." — a player
 * that looks broken rather than one offering a download.
 */

/**
 * Whether the transport controls do anything.
 *
 * Streaming makes "is it downloaded" the wrong question everywhere except Cast.
 * `LocalCastHttpServer` serves the *phone's* copy to the receiver over the LAN;
 * there is no server-side Cast path (the receiver cannot send a Bearer token),
 * so a book that is only being streamed cannot be cast at all.
 */
fun transportEnabled(isDownloaded: Boolean, isOnline: Boolean, isCasting: Boolean): Boolean =
    if (isCasting) isDownloaded else isDownloaded || isOnline

/**
 * Whether to offer the Cast button. Same reason as above: casting an
 * undownloaded book would hand the receiver a URL it cannot authenticate, so
 * the button is disabled rather than left to fail on the television.
 */
fun castAvailable(isDownloaded: Boolean): Boolean = isDownloaded

/**
 * What to say above the download button, or null when there is nothing to say.
 *
 * Not an error any more: streaming is the normal case, and the download is an
 * offer (offline listening, and Cast). It only becomes a warning when the
 * device is offline and there is no local copy, which is the one state where
 * the controls really are dead.
 */
fun downloadHintMessage(isDownloaded: Boolean, isOnline: Boolean): String? = when {
    isDownloaded -> null
    isOnline -> "Streaming from your server. Download for offline listening and casting."
    else -> "Offline — download this audiobook to listen without a connection."
}
