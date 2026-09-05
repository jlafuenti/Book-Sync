/**
 * The web client's position-write rules, in one place (issue #274).
 *
 * `docs/position-sync-contract.md` says `PUT /api/sync/position/{scope}/{ident}`
 * is the **only** write path, and that a wrong write fails silently — you find
 * out by reopening the book at the wrong page. The rules that make a write
 * correct (which scope, the device triple, who may claim `source`, and what
 * counts as a conflict) used to be hand-copied at eight call sites across four
 * files, with `positionTarget` alone existing in four different shapes. Adding
 * a field to the contract meant finding every one of them, and missing one gave
 * a write that was silently mis-adjudicated rather than an error.
 *
 * Web was the only one of the three clients where this had no single testable
 * unit. Android has `PositionSavePolicy`; this is the web's.
 *
 * Every position write in `web/src` goes through `writePosition` or
 * `keepalivePosition`. Nothing else should call `api.updatePosition` directly.
 */

import { updatePosition, sendPositionKeepalive, getDeviceId, getDeviceName } from '../api'

/**
 * Which canonical record a write for this book addresses.
 *
 * A paired book has ONE record shared by the reader and the player, so the two
 * cannot drift apart; an unpaired one gets its own standalone record for its
 * own media scope.
 *
 * The pair id arrives under three spellings depending on where the object came
 * from — `pairId` on the player's audiobook, `pair_id` on a library detail
 * response, `book_pair_id` on a progress row — so all three are accepted rather
 * than making every caller normalise first.
 *
 * @param {object|null} book       anything carrying a pair id, or null
 * @param {string} mediaType       'ebook' | 'audiobook', used when unpaired
 * @param {number} [mediaId]       the media row id; defaults to `book.id`
 * @returns {[string, number]} `[scope, ident]` for the position endpoint
 */
export function positionTarget(book, mediaType, mediaId) {
    // `??` deliberately not used for the pair id: ids are 1-based, so a 0 here
    // means "unset" and must fall through to the media scope rather than
    // addressing `pair/0`.
    const pairId = book?.pairId || book?.pair_id || book?.book_pair_id
    if (pairId) return ['pair', pairId]
    return [mediaType, mediaId !== undefined && mediaId !== null ? mediaId : book?.id]
}

/**
 * Device attribution + write ordering, sent with every position write (#54).
 *
 * `captured_at` is read fresh per call on purpose: a batch of `Promise.all`
 * writes must each stamp their own moment, and the server's staleness verdict
 * is decided on this value (bounded by MAX_CLOCK_SKEW — contract, "The write
 * gate").
 */
export function deviceMeta() {
    return {
        device_id: getDeviceId(),
        device_name: getDeviceName(),
        captured_at: new Date().toISOString(),
    }
}

/**
 * Read a stale-write rejection, or null if there is nothing to show the user.
 *
 * A rejection carrying *this* device's id is this device's own retried or
 * out-of-order write bouncing off itself — not a conflict, and surfacing it
 * would tell the user another device changed something when nothing did.
 *
 * The cfi is taken only from an `epubjs_cfi` hint marked `current`: a hint is
 * current iff its `anchor_revision` still matches the bookmark's, and a stale
 * one addresses a DOM that has moved (contract, "Anchors and hints"). Callers
 * that have no reader use only `deviceName`/`audioPositionMs`.
 *
 * @returns {{cfi: string|null, deviceName: string, audioPositionMs: number}|null}
 */
export function conflictFrom(result) {
    if (!result?.rejected || !result.device_id) return null
    if (result.device_id === getDeviceId()) return null

    const currentCfiHint = (result.hints || []).find(h => h.kind === 'epubjs_cfi' && h.current)
    return {
        cfi: currentCfiHint ? currentCfiHint.value : null,
        deviceName: result.device_name || result.device_id,
        audioPositionMs: result.audio_position_ms || 0,
    }
}

/**
 * Assemble a contract-shaped payload. `source` comes from the option and only
 * from the option — a `source` key smuggled in through `fields` would route
 * round the "who may claim `source`" rule silently, so it is dropped.
 *
 * `captured_at` *is* overridable through `fields`: EbookReader stamps the page
 * turn and only then awaits the sync-map match, so its write attests when the
 * reader was there rather than when the round-trip finished.
 */
function payloadFor(fields, claimSource) {
    const { source: _ignored, ...rest } = fields || {}
    const payload = { ...deviceMeta(), ...rest }
    if (claimSource) payload.source = claimSource
    return payload
}

/**
 * The one position write.
 *
 * @param {[string, number]} target  from `positionTarget`
 * @param {object} fields            anchors, `is_completed`, `hint`, `append_to_log`, …
 * @param {object} [opts]
 * @param {string|null} [opts.claimSource]  'ebook' | 'audiobook' when this save
 *   may claim the format — actively playing, an explicit user command, or a
 *   reader save (contract, "Who may claim `source`"). Omitted means "keep the
 *   stored value", which is what a background save must do.
 * @returns {Promise<object>} the server's position record, unchanged, so the
 *   caller can pass it to `conflictFrom`.
 */
export function writePosition(target, fields, { claimSource = null } = {}) {
    const [scope, ident] = target
    return updatePosition(scope, ident, payloadFor(fields, claimSource))
}

/**
 * The one deliberate exception to `writePosition` (contract, "Save cadence").
 *
 * A tab close aborts an ordinary fetch mid-flight, so the final flush has to go
 * out as a `keepalive` request that outlives the document. It cannot share
 * `writePosition`'s transport for that reason alone — the payload rules are
 * identical, which is why it lives here and not inline in the player.
 * `sendBeacon` is not an option: the canonical endpoint is PUT, not POST.
 */
export function keepalivePosition(target, fields, { claimSource = null } = {}) {
    const [scope, ident] = target
    return sendPositionKeepalive(scope, ident, payloadFor(fields, claimSource))
}
