package com.booksync.ui.reader

/**
 * The fraction of the book's total progression the walkthrough opens a book
 * to, when it opens one at all (issue #597 follow-up). Tester feedback: the
 * tour's "sync a sentence" step opened on the cover, where there is nothing
 * to select — so the reader should land a few percent in, past the cover,
 * copyright and dedication pages an audiobook usually skips too.
 */
private const val TOUR_READER_JUMP_PROGRESSION = 0.05

/**
 * Whether — and where — the tour should nudge a freshly-opened reader, past
 * the front matter.
 *
 * Returns [TOUR_READER_JUMP_PROGRESSION] only when every one of these holds,
 * and null otherwise:
 * - the walkthrough is actually running a Reader-screen step right now
 *   ([tourRunningOnReader]) — an ordinary, non-tour open must never jump;
 * - that step's pair ([tourPairId]) is the very pair this reader just opened
 *   ([thisPairId]) — a stale or unrelated tour pair id must not move a
 *   reader the tour isn't pointing at;
 * - the book has **no** saved position ([hasSavedPosition] false) — a real
 *   reader's place, however it was arrived at, is never moved. A fresh book
 *   (or one whose stored position could not be resolved on this device)
 *   lands on the first spine item exactly like a genuinely unread one, so
 *   the jump is safe there.
 *
 * Pure and total: no I/O, no Android types, nothing to fake in a test.
 */
fun tourJumpTarget(
    tourRunningOnReader: Boolean,
    tourPairId: Int?,
    thisPairId: Int,
    hasSavedPosition: Boolean,
): Double? {
    if (!tourRunningOnReader) return null
    if (tourPairId != thisPairId) return null
    if (hasSavedPosition) return null
    return TOUR_READER_JUMP_PROGRESSION
}
