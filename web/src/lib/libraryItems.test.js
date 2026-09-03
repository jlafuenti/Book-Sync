import { describe, it, expect } from 'vitest'
import { toDisplayEntry, entryKey, groupProgressByPair, patchMedia } from './libraryItems'
import { pairTargetPath } from '../utils/pairRouting'

// Issue #120: LibraryPage renders `LibraryItem`s from /api/library/items.
// These helpers turn one into the flat "entry" shape the cards, rows,
// selection and bulk code already consumed when the page assembled the list
// itself — so the render side of the page did not have to change.

const ebook = { id: 10, title: 'Ship of Magic', author: 'Robin Hobb', series: 'Liveship', series_index: 1,
    cover_path: '/api/files/covers/ebook_10.jpg', uploaded_at: '2026-01-01T00:00:00Z', file_size: 100, format: 'epub' }
const audiobook = { id: 20, title: 'Ship of Magic (audio)', author: 'R. Hobb', series: null, series_index: null,
    cover_path: '/api/files/covers/audiobook_20.jpg', uploaded_at: '2026-02-01T00:00:00Z', file_size: 1000, format: 'm4b' }
const pair = { id: 7, ebook, audiobook, status: 'synced', acknowledged: true }

describe('toDisplayEntry', () => {
    it('a pair is one entry, ebook-primary, carrying both ids and the raw pair', () => {
        const e = toDisplayEntry({ kind: 'pair', pair, ebook: null, audiobook: null }, {})
        expect(e.mediaType).toBe('pair')
        expect(e.id).toBe(10)                 // primary = ebook, as before
        expect(e.pair_id).toBe(7)
        expect(e.pair_status).toBe('synced')
        expect(e.ebook_id).toBe(10)
        expect(e.audiobook_id).toBe(20)
        expect(e.title).toBe('Ship of Magic')
        expect(e.author).toBe('Robin Hobb')
        expect(e.series).toBe('Liveship')
        expect(e.cover_path).toBe('/api/files/covers/ebook_10.jpg')
        expect(e.pair).toBe(pair)
        expect(e.lastFormat).toBeNull()
    })

    it('a pair falls back to the audiobook for fields the ebook lacks', () => {
        const bare = { ...ebook, cover_path: null, author: null, series: null }
        const e = toDisplayEntry({ kind: 'pair', pair: { ...pair, ebook: bare } }, {})
        expect(e.cover_path).toBe('/api/files/covers/audiobook_20.jpg')
        expect(e.author).toBe('R. Hobb')
    })

    // Issue #215: the routing key is `bookmarks.source`, projected onto both
    // progress rows. The `updated_at` comparison this replaced always tied,
    // because a pair-scoped write stamps both rows in the same loop.
    it('a pair takes its source from the progress grouped by pair id', () => {
        const sameInstant = '2026-03-01T00:00:00Z'
        const byPair = { 7: [
            { media_type: 'ebook', source: 'audiobook', updated_at: sameInstant },
            { media_type: 'audiobook', source: 'audiobook', updated_at: sameInstant },
        ] }
        const e = toDisplayEntry({ kind: 'pair', pair }, byPair)
        expect(e.lastFormat).toBe('audiobook')
    })

    it('a paired ebook keeps its own fields and gains pair_id / pair_status / paired_audiobook_id', () => {
        const e = toDisplayEntry({ kind: 'ebook', ebook, pair }, {})
        expect(e.mediaType).toBe('ebook')
        expect(e.id).toBe(10)
        expect(e.pair_id).toBe(7)
        expect(e.pair_status).toBe('synced')
        expect(e.paired_audiobook_id).toBe(20)
        expect(e.title).toBe('Ship of Magic')
    })

    it('an unpaired audiobook has null pairing fields', () => {
        const e = toDisplayEntry({ kind: 'audiobook', audiobook, pair: null }, {})
        expect(e.mediaType).toBe('audiobook')
        expect(e.pair_id).toBeNull()
        expect(e.pair_status).toBeNull()
        expect(e.paired_ebook_id).toBeNull()
        expect(e.file_size).toBe(1000)
    })
})

describe('entryKey', () => {
    it('is pair-<pairId> for pairs and <type>-<id> for media — the selection key', () => {
        expect(entryKey(toDisplayEntry({ kind: 'pair', pair }, {}))).toBe('pair-7')
        expect(entryKey(toDisplayEntry({ kind: 'ebook', ebook }, {}))).toBe('ebook-10')
        expect(entryKey(toDisplayEntry({ kind: 'audiobook', audiobook }, {}))).toBe('audiobook-20')
    })
})

describe('groupProgressByPair', () => {
    it('groups by book_pair_id, falling back to the media→pair maps of the loaded items', () => {
        const items = [{ kind: 'pair', pair }]
        const progress = [
            { book_pair_id: 7, media_type: 'ebook', updated_at: '2026-01-01T00:00:00Z' },
            { book_pair_id: null, media_type: 'audiobook', audiobook_id: 20, updated_at: '2026-02-01T00:00:00Z' },
            { book_pair_id: null, media_type: 'ebook', ebook_id: 999, updated_at: '2026-02-01T00:00:00Z' },
        ]
        const byPair = groupProgressByPair(progress, items)
        expect(byPair[7]).toHaveLength(2)
        expect(Object.keys(byPair)).toEqual(['7'])
    })

    it('copes with no progress', () => {
        expect(groupProgressByPair(null, [])).toEqual({})
    })
})

describe('patchMedia', () => {
    const items = [
        { kind: 'pair', pair, ebook: null, audiobook: null },
        { kind: 'ebook', ebook: { ...ebook, id: 11, title: 'Mad Ship' }, pair: null },
        { kind: 'audiobook', audiobook: { ...audiobook, id: 21 }, pair: null },
    ]

    it('applies a metadata patch to a medium wherever it appears (top level or inside a pair)', () => {
        const out = patchMedia(items, 'ebook', 10, { author: 'Megan Lindholm' })
        expect(out[0].pair.ebook.author).toBe('Megan Lindholm')
        expect(out[1].ebook.author).toBe('Robin Hobb')     // untouched
        expect(out).not.toBe(items)                          // new array, no mutation
        expect(items[0].pair.ebook.author).toBe('Robin Hobb')
    })

    it('patches a standalone medium', () => {
        const out = patchMedia(items, 'audiobook', 21, { series: 'Liveship' })
        expect(out[2].audiobook.series).toBe('Liveship')
    })
})

// The Library's pair-tap: an entry's `lastFormat` (the pair's
// `bookmarks.source`) plus pairTargetPath decides which detail page opens.
// Issue #215 — this used to be driven by two always-equal timestamps, so the
// Library, like Home, always opened the ebook.
describe('a pair entry routes on its source, not on timestamps', () => {
    const sameInstant = '2026-03-01T00:00:00Z'
    const rows = (source) => ({ 7: [
        { media_type: 'ebook', source, updated_at: sameInstant },
        { media_type: 'audiobook', source, updated_at: sameInstant },
    ] })
    const entryFor = (source) => toDisplayEntry({ kind: 'pair', pair }, rows(source))

    it('source=audiobook opens the audiobook', () => {
        const e = entryFor('audiobook')
        expect(pairTargetPath(e, e.lastFormat)).toBe('/book/audiobook/20')
    })

    it('source=ebook opens the ebook', () => {
        const e = entryFor('ebook')
        expect(pairTargetPath(e, e.lastFormat)).toBe('/book/ebook/10')
    })

    it('no source falls back to the ebook', () => {
        const e = entryFor(undefined)
        expect(e.lastFormat).toBeNull()
        expect(pairTargetPath(e, e.lastFormat)).toBe('/book/ebook/10')
    })
})
