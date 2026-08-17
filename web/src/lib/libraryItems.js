import { lastFormatFromProgress } from '../utils/pairRouting'

/**
 * Adapters between `LibraryItem`s (what `GET /api/library/items` returns —
 * `{kind, pair, ebook, audiobook}`, issue #120) and the flat "entry" shape
 * LibraryPage's cards, rows, selection and bulk actions consume.
 *
 * The page used to build these entries itself from three whole-library
 * arrays; the shape is kept so the render side did not have to change.
 */

/** Selection key: `pair-<pairId>` for a pair, `<mediaType>-<id>` otherwise. */
export function entryKey(entry) {
    if (entry.mediaType === 'pair') return `pair-${entry.pair_id}`
    return `${entry.mediaType}-${entry.id}`
}

/**
 * Group progress rows by pair id so a pair entry can carry `lastFormat`
 * (which side the user last used — decides where a tap lands). Rows that
 * predate `book_pair_id` are mapped through the loaded items' media ids.
 */
export function groupProgressByPair(progress, items) {
    const ebookToPair = {}
    const audiobookToPair = {}
    for (const it of items || []) {
        const p = it.pair
        if (!p) continue
        if (p.ebook?.id) ebookToPair[p.ebook.id] = p.id
        if (p.audiobook?.id) audiobookToPair[p.audiobook.id] = p.id
    }
    const byPair = {}
    for (const rec of progress || []) {
        let pid = rec.book_pair_id
        if (!pid) {
            if (rec.media_type === 'ebook' && rec.ebook_id) pid = ebookToPair[rec.ebook_id]
            if (rec.media_type === 'audiobook' && rec.audiobook_id) pid = audiobookToPair[rec.audiobook_id]
        }
        if (!pid) continue
        ;(byPair[pid] ||= []).push(rec)
    }
    return byPair
}

/** One LibraryItem → one display entry. `progressByPair` from groupProgressByPair. */
export function toDisplayEntry(item, progressByPair = {}) {
    if (item.kind === 'pair') {
        const p = item.pair
        const eb = p.ebook || null
        const ab = p.audiobook || null
        // Ebook-primary for metadata/cover, audiobook as the fallback.
        const primary = eb || ab || {}
        return {
            ...primary,
            mediaType: 'pair',
            cover_path: eb?.cover_path || ab?.cover_path || null,
            title: eb?.title || ab?.title,
            author: eb?.author || ab?.author || null,
            series: eb?.series || ab?.series || null,
            series_index: eb?.series_index ?? ab?.series_index ?? null,
            uploaded_at: eb?.uploaded_at || ab?.uploaded_at,
            pair_id: p.id,
            pair_status: p.status,
            ebook_id: eb?.id ?? null,
            audiobook_id: ab?.id ?? null,
            // 'audiobook' | 'ebook' | null — drives where a click lands.
            lastFormat: lastFormatFromProgress(progressByPair[p.id]),
            pair: p,
        }
    }
    const media = item[item.kind]
    const p = item.pair || null
    const entry = {
        ...media,
        mediaType: item.kind,
        pair_id: p?.id ?? null,
        pair_status: p?.status ?? null,
        pair: p,
    }
    if (item.kind === 'ebook') entry.paired_audiobook_id = p?.audiobook?.id ?? null
    else entry.paired_ebook_id = p?.ebook?.id ?? null
    return entry
}

/**
 * Apply a metadata patch to one medium wherever it appears in the loaded
 * items — as a top-level ebook/audiobook item or nested in a pair — so an
 * edit shows immediately without refetching. Returns a new array.
 */
export function patchMedia(items, mediaType, id, patch) {
    const fix = (m) => (m && m.id === id ? { ...m, ...patch } : m)
    return items.map((it) => {
        let next = it
        if (it[mediaType] && it[mediaType].id === id) next = { ...next, [mediaType]: fix(it[mediaType]) }
        if (it.pair && it.pair[mediaType] && it.pair[mediaType].id === id) {
            next = { ...next, pair: { ...it.pair, [mediaType]: fix(it.pair[mediaType]) } }
        }
        return next
    })
}
