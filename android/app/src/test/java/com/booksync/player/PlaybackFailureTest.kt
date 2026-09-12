package com.booksync.player

import androidx.media3.common.PlaybackException
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * What the listener is told when playback fails (issue #475).
 *
 * Until this existed, a failure was observed by nobody: `AudioPlayerService`
 * registered a `Player.Listener` with no `onPlayerError`, so a decoder failure,
 * a truncated download or an unreachable server all ended in the same place —
 * cover art, `0:00`, and a Play button that did nothing, forever, with no
 * explanation and nothing to act on.
 *
 * The mapping lives here rather than in the service or the screen because both
 * of those are excluded from Kover; a rule nobody can cover is a rule that
 * rots. The wiring that carries it to the user is pinned separately by
 * [PlaybackErrorWiringTest].
 *
 * The recovery matters as much as the message: re-downloading a file the device
 * has no codec for wastes the user's data and still doesn't play, and retrying
 * a stream whose bytes are corrupt on disk replays the same broken bytes.
 */
class PlaybackFailureTest {

    // --- The network is the problem: retrying is the whole remedy ---

    @Test
    fun `a failed connection asks the listener to try again`() {
        val failure = playbackFailureFor(
            PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_FAILED,
            isDownloaded = false,
        )
        assertEquals(PlaybackRecovery.RETRY, failure.recovery)
        assertTrue(
            "The message must name the connection, not the file: ${failure.message}",
            failure.message.contains("connection", ignoreCase = true),
        )
    }

    @Test
    fun `a connection timeout is the same case as a failed connection`() {
        assertEquals(
            playbackFailureFor(
                PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_FAILED,
                isDownloaded = false,
            ),
            playbackFailureFor(
                PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_TIMEOUT,
                isDownloaded = false,
            ),
        )
    }

    /**
     * A media token lasts an hour; a stream outliving one comes back as 401.
     * Retrying re-mints it, so this is recoverable — and saying "session" beats
     * blaming the network for something the network did fine.
     */
    @Test
    fun `a rejected request points at the session, and retrying re-mints it`() {
        val failure = playbackFailureFor(
            PlaybackException.ERROR_CODE_IO_BAD_HTTP_STATUS,
            isDownloaded = false,
        )
        assertEquals(PlaybackRecovery.RETRY, failure.recovery)
        assertTrue(
            "The message must not blame the connection — the server answered: ${failure.message}",
            failure.message.contains("session", ignoreCase = true),
        )
    }

    // --- The file is the problem: retrying replays the same bad bytes ---

    @Test
    fun `a missing file asks for the download again, not a retry`() {
        val failure = playbackFailureFor(
            PlaybackException.ERROR_CODE_IO_FILE_NOT_FOUND,
            isDownloaded = true,
        )
        assertEquals(PlaybackRecovery.REDOWNLOAD, failure.recovery)
        assertTrue(
            "The message must say the file is missing: ${failure.message}",
            failure.message.contains("missing", ignoreCase = true),
        )
    }

    @Test
    fun `a malformed download asks for the download again`() {
        assertEquals(
            PlaybackRecovery.REDOWNLOAD,
            playbackFailureFor(
                PlaybackException.ERROR_CODE_PARSING_CONTAINER_MALFORMED,
                isDownloaded = true,
            ).recovery,
        )
        assertEquals(
            PlaybackRecovery.REDOWNLOAD,
            playbackFailureFor(
                PlaybackException.ERROR_CODE_DECODING_FAILED,
                isDownloaded = true,
            ).recovery,
        )
    }

    /**
     * The same bad bytes arriving over the wire are not a download to replace —
     * there is nothing on the phone to delete. Offering "Download again" there
     * would be an action that cannot be taken.
     */
    @Test
    fun `malformed bytes while streaming are a retry, not a re-download`() {
        val failure = playbackFailureFor(
            PlaybackException.ERROR_CODE_PARSING_CONTAINER_MALFORMED,
            isDownloaded = false,
        )
        assertEquals(PlaybackRecovery.RETRY, failure.recovery)
    }

    /**
     * Found on an emulator, against a downloaded file overwritten with random
     * bytes: media3 raises `ERROR_CODE_PARSING_CONTAINER_UNSUPPORTED` (3003),
     * not one of the malformed codes — `UnrecognizedInputFormatException`, "none
     * of the available extractors could read the stream". Nothing can sniff what
     * is not a container at all.
     *
     * Reading that as an unsupported format blames the listener's phone for a
     * file that is merely broken, and leaves them with no button. What could not
     * be *recognised* is suspect bytes; what parsed fine but could not be
     * *decoded* is the device. That line is what separates this from the codec
     * cases below.
     */
    @Test
    fun `a downloaded file no extractor recognises is a broken download`() {
        val failure = playbackFailureFor(
            PlaybackException.ERROR_CODE_PARSING_CONTAINER_UNSUPPORTED,
            isDownloaded = true,
        )
        assertEquals(PlaybackRecovery.REDOWNLOAD, failure.recovery)
        assertTrue(
            "The message must not blame the device for a broken file: ${failure.message}",
            !failure.message.contains("device", ignoreCase = true),
        )
    }

    @Test
    fun `an unrecognised container while streaming really is the format`() {
        assertEquals(
            PlaybackRecovery.NONE,
            playbackFailureFor(
                PlaybackException.ERROR_CODE_PARSING_CONTAINER_UNSUPPORTED,
                isDownloaded = false,
            ).recovery,
        )
    }

    // --- The device is the problem: neither remedy applies ---

    /**
     * The failure that exposed all of this: `MediaCodecAudioRenderer error` on
     * an emulator with no AAC decoder. Re-downloading fetches the same bytes the
     * device already cannot decode, and retrying initialises the same absent
     * codec — so the honest answer is to explain and offer nothing.
     */
    @Test
    fun `a codec this device lacks offers no action, downloaded or not`() {
        for (downloaded in listOf(true, false)) {
            val failure = playbackFailureFor(
                PlaybackException.ERROR_CODE_DECODER_INIT_FAILED,
                isDownloaded = downloaded,
            )
            assertEquals(
                "A missing codec is not fixed by re-downloading (downloaded=$downloaded)",
                PlaybackRecovery.NONE,
                failure.recovery,
            )
            assertTrue(
                "The message must say it is the device: ${failure.message}",
                failure.message.contains("device", ignoreCase = true),
            )
        }
    }

    @Test
    fun `an unsupported format is the same case as a missing decoder`() {
        assertEquals(
            PlaybackRecovery.NONE,
            playbackFailureFor(
                PlaybackException.ERROR_CODE_DECODING_FORMAT_UNSUPPORTED,
                isDownloaded = true,
            ).recovery,
        )
        assertEquals(
            PlaybackRecovery.NONE,
            playbackFailureFor(
                PlaybackException.ERROR_CODE_DECODING_FORMAT_EXCEEDS_CAPABILITIES,
                isDownloaded = true,
            ).recovery,
        )
    }

    /**
     * Codecs reclaimed under memory pressure read like an unsupported format but
     * are not one — the decoder was taken away, and asking for it again gets it
     * back. This is the case a coarse "anything in the 4xxx range is hopeless"
     * rule would strand.
     */
    @Test
    fun `a decoder reclaimed under memory pressure is worth retrying`() {
        assertEquals(
            PlaybackRecovery.RETRY,
            playbackFailureFor(
                PlaybackException.ERROR_CODE_DECODING_RESOURCES_RECLAIMED,
                isDownloaded = true,
            ).recovery,
        )
    }

    // --- The invariant ---

    /**
     * **No error code may resolve to silence.** This is the whole point: the bug
     * was not a bad message, it was no message, and a mapper with a `when` that
     * quietly falls through to an empty string would reproduce it exactly.
     *
     * The sweep covers every code media3 defines today and every code it might
     * define tomorrow, plus the custom range apps and the Cast receiver can
     * raise, in both download states.
     */
    @Test
    fun `every error code yields something to show the user`() {
        for (code in 1000..7000) {
            for (downloaded in listOf(true, false)) {
                val failure = playbackFailureFor(code, isDownloaded = downloaded)
                assertTrue(
                    "code $code (downloaded=$downloaded) produced no message",
                    failure.message.isNotBlank(),
                )
            }
        }
    }

    @Test
    fun `an unrecognised code still offers a retry`() {
        val failure = playbackFailureFor(
            PlaybackException.ERROR_CODE_UNSPECIFIED,
            isDownloaded = false,
        )
        assertEquals(PlaybackRecovery.RETRY, failure.recovery)
        assertTrue(failure.message.isNotBlank())
    }
}
