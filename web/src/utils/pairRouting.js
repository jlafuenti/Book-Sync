/**
 * Decide where a click on a paired book should land based on what the user
 * last did with the pair.
 *
 * @param {Object} pair         An object with at least `ebook_id` and/or `audiobook_id`.
 * @param {string|null} lastFormat  'audiobook' | 'ebook' | null/undefined.
 *                                  Typically `bookmark.source`, or computed from
 *                                  per-format progress timestamps.
 * @returns {string|null}       Path to navigate to, or null when neither side exists.
 */
export function pairTargetPath(pair, lastFormat) {
    if (!pair) return null
    if (lastFormat === 'audiobook' && pair.audiobook_id) {
        return `/book/audiobook/${pair.audiobook_id}`
    }
    if (lastFormat === 'ebook' && pair.ebook_id) {
        return `/book/ebook/${pair.ebook_id}`
    }
    // Fallback — matches today's hardcoded behavior (prefer ebook).
    if (pair.ebook_id)     return `/book/ebook/${pair.ebook_id}`
    if (pair.audiobook_id) return `/book/audiobook/${pair.audiobook_id}`
    return null
}

/**
 * Compute the "last used" format for a pair from a list of progress records.
 * Returns 'audiobook' | 'ebook' | null.
 *
 * @param {Array} progressRecords  Records with `media_type` ('ebook' | 'audiobook')
 *                                 and `updated_at` (ISO string).
 */
export function lastFormatFromProgress(progressRecords) {
    if (!progressRecords || progressRecords.length === 0) return null
    const ebook = progressRecords.find(p => p.media_type === 'ebook')
    const audio = progressRecords.find(p => p.media_type === 'audiobook')
    const ebookT = ebook ? new Date(ebook.updated_at).getTime() : 0
    const audioT = audio ? new Date(audio.updated_at).getTime() : 0
    if (ebookT === 0 && audioT === 0) return null
    return audioT > ebookT ? 'audiobook' : 'ebook'
}
