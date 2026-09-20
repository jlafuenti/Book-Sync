package com.booksync.data.sync

/**
 * The audio-start ladder — where playback begins for a book, given the position
 * record (issue #643).
 *
 * This is the counterpart to [planRestore], the reader's ladder. Every
 * [RestoreStep] yields an *ebook* target; `RestoreStep.Audio` even runs
 * backwards, audio -> epub. Nothing yielded an audio position from an ebook
 * anchor, so the player read `audioPositionMs` and nothing else — which is the
 * wrong number whenever the last thing the user did was read.
 *
 * The failure it exists to stop: read the ebook, get in the car, pick the book,
 * and Android Auto starts wherever the audiobook was last played. The chapter,
 * the sentence index and the text preview describing the real position were in
 * the same row the whole time, and the sync map was already cached on the
 * device.
 *
 * **The three rungs are the same either way; `source` only reorders them.**
 * `source` is the last *deliberate* format — a reader save stamps `ebook`, and
 * `savePlaybackPosition` stamps `audiobook` only for active playback or an
 * explicit user command, so heartbeats from a paused player do not claim it.
 * That makes it exactly the right discriminator, and the same one
 * `resolvePairOpenTarget` already routes on.
 *
 * Executing a step needs the cached sync points, so that stays in
 * `PositionRepository.audioStartMsFor`; the ordering lives here, where it can
 * be tested without a database.
 */

const val SOURCE_EBOOK = "ebook"
const val SOURCE_AUDIOBOOK = "audiobook"

/** One rung of the ladder, carrying what the executor needs to act on it. */
sealed class AudioStartStep {
    abstract val kind: String

    /**
     * Find this text in the sync map and take its point. The most reliable
     * rung: it survives re-parsing and re-alignment, which is why the restore
     * ladder ranks text above chapter too.
     */
    data class Text(val text: String, val seedChapter: Int?) : AudioStartStep() {
        override val kind = "text"
    }

    /**
     * Take the sync point for this chapter and sentence.
     *
     * The executor must stay **inside [chapter]**. The stored sentence index is
     * not guaranteed to belong to the stored chapter: on a sync-point miss
     * `saveReaderPosition` writes the new chapter but inherits the previous
     * sentence index (issue #644). Walking backwards across chapters the way
     * the server's `epub_to_audio` does would turn that mismatch into a
     * confidently wrong seek; staying in the chapter bounds the error to the
     * top of the right chapter.
     */
    data class Sentence(val chapter: Int, val sentenceIndex: Int?) : AudioStartStep() {
        override val kind = "sentence"
    }

    /** The audio position the record already holds. */
    data class Stored(val audioPositionMs: Int) : AudioStartStep() {
        override val kind = "stored"
    }
}

/**
 * The rungs to try, in order. An empty list means "start of the book" — the
 * record offers nothing at all.
 *
 * A derivation that fails falls through to [AudioStartStep.Stored] rather than
 * to zero: a stale listening position is a worse answer than the reading
 * position, but a far better one than the beginning of the book.
 */
fun planAudioStart(position: StoredPosition): List<AudioStartStep> {
    val stored = position.audioPositionMs
        ?.takeIf { it > 0 }
        ?.let { AudioStartStep.Stored(it) }

    // Below MIN_SEARCHABLE_PREVIEW the text matches too much to place — the
    // same threshold the reader's text rung uses.
    val text = position.epubTextPreview
        ?.takeIf { it.length >= MIN_SEARCHABLE_PREVIEW }
        ?.let { AudioStartStep.Text(it, position.epubChapter) }

    val sentence = position.epubChapter
        ?.let { AudioStartStep.Sentence(it, position.epubSentenceIndex) }

    return if (position.source == SOURCE_AUDIOBOOK) {
        // Listening was the last deliberate act: the stored position is the
        // truth. The ebook rungs stay as a fallback for a record that claims
        // audiobook but carries no usable audio position — a first write, or
        // one that only ever reached 0.
        listOfNotNull(stored, text, sentence)
    } else {
        listOfNotNull(text, sentence, stored)
    }
}
