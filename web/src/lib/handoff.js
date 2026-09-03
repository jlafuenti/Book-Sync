import { updatePosition, getDeviceId, getDeviceName } from '../api'

/**
 * The audio -> text half of the format handoff (issue #267).
 *
 * Three call sites used to carry their own copy of this: HomePage,
 * BookDetailPage, and — after #267 — the app-wide mini-player, which is the
 * player every route other than Home and BookDetail gets. Three copies of a
 * position write is three chances to get `source` or the device metadata wrong,
 * and the failure mode (reopening at the wrong page) is silent.
 *
 * `source: 'audiobook'` is claimed deliberately: this is an explicit user
 * command to move to the text at the place the audio reached, which
 * `docs/position-sync-contract.md` ("Who may claim `source`") allows.
 *
 * Pausing comes first, so the audio does not keep running under the reader and
 * the playback heartbeat cannot write a newer position over the handoff.
 *
 * @param {object} player  the AudioPlayerContext value (needs pause, currentTime)
 * @param {number} pairId  the pair whose canonical position is being written
 * @returns {Promise<object|null>} the server's position record, or null if the
 *   write did not land — the caller opens the reader either way.
 */
export async function switchToEbook(player, pairId) {
    player.pause()
    const audioPositionMs = Math.floor((player.currentTime || 0) * 1000)
    return updatePosition('pair', pairId, {
        source: 'audiobook',
        audio_position_ms: audioPositionMs,
        device_id: getDeviceId(),
        device_name: getDeviceName(),
        captured_at: new Date().toISOString(),
    }).catch(() => null)
}
