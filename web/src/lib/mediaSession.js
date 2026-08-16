/**
 * Thin, defensive wrappers around `navigator.mediaSession` (issue #62).
 *
 * The Media Session API is what puts play/pause/skip controls, the title and
 * the cover on a phone's lock screen and notification shade, and lets
 * hardware media keys drive the web player. It is absent in jsdom and in
 * some browsers, and the parts that exist throw for unsupported actions or
 * out-of-range position state — so every helper here feature-detects and
 * swallows, and the player context can call them unconditionally.
 *
 * Kept free of React so the browser-API edge cases are testable on their own
 * (`mediaSession.test.js`); the wiring to the audio element lives in
 * `contexts/AudioPlayerContext.jsx`.
 */

// The actions the player binds. Order matters only for readability. Skip
// amounts are the player's own SKIP_SECONDS (symmetric 30 s per
// docs/position-sync-contract.md § Playback offsets) — the handlers passed
// in decide that, not this module.
export const MEDIA_SESSION_ACTIONS = ['play', 'pause', 'stop', 'seekbackward', 'seekforward', 'seekto']

function session() {
    if (typeof navigator === 'undefined') return null
    return navigator.mediaSession || null
}

export function mediaSessionAvailable() {
    return session() !== null
}

/** Title / author / cover shown by the OS. `artworkUrl` may be null. */
export function applyMetadata({ title, artist, artworkUrl }) {
    const ms = session()
    if (!ms || typeof MediaMetadata === 'undefined') return
    try {
        ms.metadata = new MediaMetadata({
            title: title || '',
            artist: artist || '',
            artwork: artworkUrl ? [{ src: artworkUrl }] : [],
        })
    } catch {
        // A browser with mediaSession but a picky MediaMetadata: no metadata,
        // controls still work.
    }
}

export function setPlaybackState(state) {
    const ms = session()
    if (!ms) return
    try {
        ms.playbackState = state
    } catch {
        // ignore
    }
}

/**
 * Feed the OS scrubber. `setPositionState` throws on a non-finite duration
 * or a position outside [0, duration]; a stream that hasn't reported its
 * length yet is skipped rather than reported as zero-length.
 */
export function applyPositionState({ duration, position, playbackRate }) {
    const ms = session()
    if (!ms || typeof ms.setPositionState !== 'function') return
    if (!Number.isFinite(duration) || duration <= 0) return
    const clamped = Math.min(Math.max(position || 0, 0), duration)
    try {
        ms.setPositionState({
            duration,
            position: clamped,
            playbackRate: Number.isFinite(playbackRate) && playbackRate > 0 ? playbackRate : 1,
        })
    } catch {
        // ignore
    }
}

/**
 * Bind (or, with `null`/absent, unbind) a handler per action. Every action in
 * MEDIA_SESSION_ACTIONS is touched so a stale handler from a previous bind
 * can't survive; a browser that rejects an action it doesn't know is skipped.
 */
export function bindActionHandlers(handlers = {}) {
    const ms = session()
    if (!ms || typeof ms.setActionHandler !== 'function') return
    for (const action of MEDIA_SESSION_ACTIONS) {
        try {
            ms.setActionHandler(action, handlers[action] || null)
        } catch {
            // unsupported action in this browser
        }
    }
}

/** Nothing is playing any more: drop metadata, state and handlers. */
export function clearMediaSession() {
    const ms = session()
    if (!ms) return
    try {
        ms.metadata = null
    } catch {
        // ignore
    }
    setPlaybackState('none')
    bindActionHandlers({})
}
