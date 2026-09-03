/**
 * Playback offsets — the web copy (issue #42).
 *
 * These two numbers must be identical on every surface: Android's
 * `PlaybackOffsets` and the server's `default_rewind_seconds` carry the same
 * ones. See `docs/position-sync-contract.md` § Playback offsets before
 * changing either.
 *
 * They live in their own module rather than inside `AudioPlayerContext` so the
 * reader → audiobook handoff sites can import the rewind without pulling the
 * whole provider (and its `../api` imports) in with it.
 */

// Both transport buttons, same in each direction.
export const SKIP_SECONDS = 30

// Picking up mid-word after a pause is hard to follow, so a resume backs up a
// few seconds first. Also the size of the text → audio handoff jump.
export const RESUME_REWIND_SECONDS = 5

/**
 * The position the reader's "switch to listening" handoff should start at
 * (issue #212).
 *
 * The contract makes the text → audio handoff a resume: it lands the same
 * RESUME_REWIND_SECONDS before the anchor that an unpause does, which is what
 * Android's `epubToAudioText` already subtracts. `play()` itself stays
 * rewind-free — Home/Continue hands it an explicit position and must not creep
 * backwards — so the rewind is applied at the handoff call sites instead.
 *
 * The *stored* anchor is untouched: a saved position describes where you were,
 * not where you resume.
 */
export function handoffPositionMs(positionMs) {
    return Math.max(0, (positionMs || 0) - RESUME_REWIND_SECONDS * 1000)
}
