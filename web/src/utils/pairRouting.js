import { resolvePairOpenTarget } from '../lib/pairOpenTarget'

/**
 * Decide where a click on a paired book should land.
 *
 * The routing rule itself lives in `lib/pairOpenTarget` — shared with Android
 * through the golden vectors in
 * `server/tests/fixtures/sync_parity/pair_open_target.json`. This wrapper only
 * turns the resolved format into a route.
 *
 * @param {Object} pair    An object with at least `ebook_id` and/or `audiobook_id`.
 * @param {string|null} source  `bookmarks.source`: 'audiobook' | 'ebook' | null.
 * @returns {string|null}  Path to navigate to, or null when neither side exists.
 */
export function pairTargetPath(pair, source) {
    if (!pair) return null
    const target = resolvePairOpenTarget(source, {
        hasEbook: !!pair.ebook_id,
        hasAudiobook: !!pair.audiobook_id,
    })
    if (target === 'audiobook') return `/book/audiobook/${pair.audiobook_id}`
    if (target === 'ebook') return `/book/ebook/${pair.ebook_id}`
    // 'details' with nothing to show: the caller has no pair page to fall back
    // to here, so there is no route.
    return null
}

/**
 * The pair's `bookmarks.source` as carried by its progress rows.
 *
 * `GET /api/sync/progress` projects the canonical bookmark's `source` onto
 * every row it produced (issue #215), so both rows of a pair report the same
 * value and either one answers.
 *
 * This replaced a comparison of the two rows' `updated_at` timestamps. A
 * pair-scoped write stamps both rows inside one loop, so those timestamps are
 * equal and the comparison always tied — resolving, every time, to the ebook.
 *
 * @param {Array} progressRecords  Records carrying `source` ('ebook' | 'audiobook').
 * @returns {string|null}
 */
export function pairSourceFromProgress(progressRecords) {
    if (!progressRecords) return null
    for (const rec of progressRecords) {
        if (rec?.source === 'ebook' || rec?.source === 'audiobook') return rec.source
    }
    return null
}
