/**
 * Which format a tap on a pair opens — the web half (issue #215).
 *
 * The router is `bookmarks.source`, per `docs/position-sync-contract.md`
 * § "Who may claim `source`": the format follows actual consumption, and only
 * a save made while that format was genuinely being consumed claims it.
 * Android's `BookSyncRepository.resolvePairOpenTarget` applies exactly these
 * rules; both are driven by the shared golden vectors in
 * `server/tests/fixtures/sync_parity/pair_open_target.json`.
 *
 * What this replaced: the web compared the two `user_progress` rows'
 * `updated_at` timestamps. A pair-scoped write projects onto *both* rows in
 * one loop, each stamped with the same `utcnow()`, so the comparison always
 * tied — and the tie resolved to the ebook. Listening on the phone and then
 * tapping the pair on the web opened the reader, every time.
 *
 * @param {string|null|undefined} source  `bookmarks.source`: 'ebook' | 'audiobook'.
 * @param {{hasEbook?: boolean, hasAudiobook?: boolean}} available
 *        Which formats this client can actually open. On the web that means
 *        present on the pair; on Android, downloaded to the device.
 * @returns {'ebook'|'audiobook'|'details'}
 */
export function resolvePairOpenTarget(source, { hasEbook = false, hasAudiobook = false } = {}) {
    if (source === 'audiobook' && hasAudiobook) return 'audiobook'
    if (source === 'ebook' && hasEbook) return 'ebook'
    // No claim, or the claimed format is not openable here: prefer the ebook,
    // then the audiobook, then neither.
    if (hasEbook) return 'ebook'
    if (hasAudiobook) return 'audiobook'
    return 'details'
}
