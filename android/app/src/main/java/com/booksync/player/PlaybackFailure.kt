package com.booksync.player

import androidx.media3.common.PlaybackException

/**
 * What a playback failure means to the listener, and what they can do about it
 * (issue #475).
 *
 * There was no answer to either question before this. `AudioPlayerService`
 * registered a `Player.Listener` that implemented `onIsPlayingChanged` and
 * friends but not `onPlayerError`, so a failure was not merely unsurfaced — it
 * was never observed. A decoder that could not start, a download truncated
 * mid-file, a file deleted out from under the player and a server that could
 * not be reached all ended identically: cover art, `0:00`, and a Play button
 * that did nothing, forever.
 *
 * The web player has told listeners this since issue #214
 * (`STREAM_ERROR_MESSAGE`, with a Retry beside it), so the two platforms
 * explained the same failure differently — one in words, one not at all.
 *
 * This mapper is a pure function on purpose. `AudioPlayerService` and
 * `PlayerScreen` are both excluded from Kover (service glue and Compose), and
 * the rule that decides what a user is told is not a rule to leave uncovered.
 */
enum class PlaybackRecovery {
    /** Ask the player again: a connection, a token, a reclaimed decoder. */
    RETRY,

    /** The bytes on the phone are the problem; fetch them again. */
    REDOWNLOAD,

    /** Nothing the listener can do — the device cannot decode this at all. */
    NONE,
}

/** A failure as the player screen shows it: one sentence, one offer. */
data class PlaybackFailure(
    val message: String,
    val recovery: PlaybackRecovery,
)

/**
 * The failure for [errorCode], given whether this book is on the phone.
 *
 * [isDownloaded] is what separates "replace the file" from "ask again": the
 * same malformed-container error means a truncated download when the bytes are
 * local and a bad response when they are not, and offering "Download again" for
 * a book that was streaming is an action the listener cannot take.
 *
 * Every code resolves to a message. That is the invariant — the bug this fixes
 * was silence, so a path through here that produces nothing to say would
 * reproduce it.
 */
fun playbackFailureFor(errorCode: Int, isDownloaded: Boolean): PlaybackFailure = when (errorCode) {

    // The network could not deliver the bytes. Nothing is wrong with the book.
    PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_FAILED,
    PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_TIMEOUT,
    PlaybackException.ERROR_CODE_IO_UNSPECIFIED,
    PlaybackException.ERROR_CODE_TIMEOUT -> PlaybackFailure(
        message = "Couldn't load the audio. Check your connection and try again.",
        recovery = PlaybackRecovery.RETRY,
    )

    // The server answered, and refused. A media token lasts an hour, so the
    // overwhelmingly likely cause is one that outlived its stream; retrying
    // mints a fresh one. Blaming the connection here sends the listener to
    // check something that is working.
    PlaybackException.ERROR_CODE_IO_BAD_HTTP_STATUS,
    PlaybackException.ERROR_CODE_IO_NO_PERMISSION -> PlaybackFailure(
        message = "The server refused the request — your session may have expired. Try again.",
        recovery = PlaybackRecovery.RETRY,
    )

    // The file the player was told to open is not there. Retrying opens the
    // same absent path.
    PlaybackException.ERROR_CODE_IO_FILE_NOT_FOUND -> PlaybackFailure(
        message = "The audio file is missing. Download it again.",
        recovery = PlaybackRecovery.REDOWNLOAD,
    )

    // The bytes arrived but do not parse or decode: a download that stopped
    // short, a file damaged on disk, a truncated response. Which remedy applies
    // depends entirely on where the bytes live.
    PlaybackException.ERROR_CODE_PARSING_CONTAINER_MALFORMED,
    PlaybackException.ERROR_CODE_PARSING_MANIFEST_MALFORMED,
    PlaybackException.ERROR_CODE_DECODING_FAILED -> if (isDownloaded) {
        PlaybackFailure(
            message = "This download can't be played — it may be incomplete. Download it again.",
            recovery = PlaybackRecovery.REDOWNLOAD,
        )
    } else {
        PlaybackFailure(
            message = "The audio stream could not be read. Try again.",
            recovery = PlaybackRecovery.RETRY,
        )
    }

    // No extractor could even recognise the stream
    // (`UnrecognizedInputFormatException`). Streaming, that is what it says: a
    // container this build cannot open. On a *downloaded* file it is far more
    // often damage — sniffing fails on truncated or corrupted bytes exactly as
    // it does on a genuinely exotic container, and the two are indistinguishable
    // from here. Verified on an emulator: overwriting a downloaded file with
    // random bytes raises 3003, not one of the malformed codes above, so putting
    // this with the codec failures told the listener their phone was at fault
    // and offered them nothing to do about it.
    PlaybackException.ERROR_CODE_PARSING_CONTAINER_UNSUPPORTED,
    PlaybackException.ERROR_CODE_PARSING_MANIFEST_UNSUPPORTED -> if (isDownloaded) {
        PlaybackFailure(
            message = "This download can't be read — it may be damaged. Download it again.",
            recovery = PlaybackRecovery.REDOWNLOAD,
        )
    } else {
        PlaybackFailure(
            message = "This device can't play this audiobook's format.",
            recovery = PlaybackRecovery.NONE,
        )
    }

    // The container parsed and the codec is the problem: the device has no
    // decoder for it. Re-downloading fetches bytes it still cannot decode and
    // retrying initialises the same absent codec, so the only honest response is
    // to say so and offer nothing. This is the case that exposed the whole bug:
    // `MediaCodecAudioRenderer error` on an emulator.
    PlaybackException.ERROR_CODE_DECODER_INIT_FAILED,
    PlaybackException.ERROR_CODE_DECODER_QUERY_FAILED,
    PlaybackException.ERROR_CODE_DECODING_FORMAT_UNSUPPORTED,
    PlaybackException.ERROR_CODE_DECODING_FORMAT_EXCEEDS_CAPABILITIES -> PlaybackFailure(
        message = "This device can't play this audiobook's format.",
        recovery = PlaybackRecovery.NONE,
    )

    // The decoder was taken away rather than missing — another app won it under
    // memory pressure. Asking again usually gets it back, so this must not fall
    // in with the codes above it despite sitting in the same range.
    PlaybackException.ERROR_CODE_DECODING_RESOURCES_RECLAIMED -> PlaybackFailure(
        message = "Playback was interrupted by another app. Try again.",
        recovery = PlaybackRecovery.RETRY,
    )

    // The audio output itself refused: another app holding it exclusively, a
    // route that disappeared mid-write.
    PlaybackException.ERROR_CODE_AUDIO_TRACK_INIT_FAILED,
    PlaybackException.ERROR_CODE_AUDIO_TRACK_WRITE_FAILED,
    PlaybackException.ERROR_CODE_AUDIO_TRACK_OFFLOAD_INIT_FAILED,
    PlaybackException.ERROR_CODE_AUDIO_TRACK_OFFLOAD_WRITE_FAILED -> PlaybackFailure(
        message = "Couldn't play to this device's audio output. Try again.",
        recovery = PlaybackRecovery.RETRY,
    )

    // Everything else, including codes media3 has not defined yet and the
    // custom range a Cast receiver can raise. A vague message the listener can
    // act on beats the silence this replaced.
    else -> PlaybackFailure(
        message = "Playback failed. Try again.",
        recovery = PlaybackRecovery.RETRY,
    )
}
