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
 * **Two axes, not one** (issue #484). This used to take a single `available`,
 * documented as "on the web that means present on the pair; on Android,
 * downloaded to the device". Issue #171 gave Android streaming and broke that
 * equivalence: an audiobook it has never downloaded is openable. Collapsing the
 * two produced one wrong answer in each direction — a book the listener had
 * streamed reopened in the reader, and a pair they had merely tapped started
 * downloading an EPUB nobody asked for.
 *
 * So: **the claim is honoured against what can be opened; with no claim, only
 * something already here is opened.** Following a claim is acting on a choice
 * the user already made. Opening something unasked is not, and a first tap must
 * not commit to a transfer — it lands on the details page, where both offers
 * are visible.
 *
 * The web passes neither `local*`, and they default to the `has*` values, so
 * every existing call site and all eight original vectors are unchanged.
 *
 * @param {string|null|undefined} source  `bookmarks.source`: 'ebook' | 'audiobook'.
 * @param {{hasEbook?: boolean, hasAudiobook?: boolean, localEbook?: boolean, localAudiobook?: boolean}} available
 *        `has*`: can this client open the format at all — present on the pair
 *        (web), or downloaded *or* streamable (Android). `local*`: is it
 *        already on this device, defaulting to `has*`.
 * @returns {'ebook'|'audiobook'|'details'}
 */
export function resolvePairOpenTarget(
    source,
    {
        hasEbook = false,
        hasAudiobook = false,
        localEbook = hasEbook,
        localAudiobook = hasAudiobook,
    } = {},
) {
    if (source === 'audiobook' && hasAudiobook) return 'audiobook'
    if (source === 'ebook' && hasEbook) return 'ebook'
    // No claim, or the claimed format cannot be opened here. Prefer the ebook,
    // then the audiobook — but only what is already on the device: with nothing
    // to go on, opening is a guess, and a guess should not start a download.
    if (localEbook) return 'ebook'
    if (localAudiobook) return 'audiobook'
    return 'details'
}
