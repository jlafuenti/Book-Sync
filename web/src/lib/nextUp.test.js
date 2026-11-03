import { describe, it, expect } from 'vitest'
import fs from 'fs'
import path from 'path'
import { computeNextUp, libraryToNextUpInput, NEXT_UP_WINDOW_DAYS } from './nextUp'

/**
 * Cross-platform parity (issue #716): this suite and Android's
 * `NextUpParityTest` load the *same* golden vectors, so the web's Next up row
 * and the app's cannot disagree about which book comes next.
 */
const FIXTURE = path.resolve(
    __dirname, '../../../server/tests/fixtures/sync_parity/next_up_cases.json'
)
const CASES = JSON.parse(fs.readFileSync(FIXTURE, 'utf-8'))

describe('computeNextUp parity vectors', () => {
    it.each(CASES.map(c => [c.name, c]))('%s', (_name, testCase) => {
        const result = computeNextUp(
            testCase.books, testCase.activity, new Date(testCase.now), testCase.dismissals ?? [],
        )

        expect(result.map(r => ({ series: r.series, key: r.key }))).toEqual(testCase.expected)
    })
})

describe('computeNextUp', () => {
    it('uses a 90-day window, the owner-chosen value', () => {
        expect(NEXT_UP_WINDOW_DAYS).toBe(90)
    })

    it('drops a series whose dismissal is at or after its last activity, and restores it after later activity', () => {
        const books = [
            { key: 'ebook_1', series: 'Axis Saga', index: 1 },
            { key: 'ebook_2', series: 'Axis Saga', index: 2 },
        ]
        const now = new Date('2026-09-26T00:00:00Z')
        const activity = [{ key: 'ebook_1', completed: true, at: '2026-09-20T00:00:00Z' }]
        const dismissals = [{ series: ' axis SAGA ', at: '2026-09-21T00:00:00Z' }]

        expect(computeNextUp(books, activity, now, dismissals)).toEqual([])
        expect(computeNextUp(books, activity, now)).toHaveLength(1)
        expect(computeNextUp(
            books, [{ key: 'ebook_1', completed: true, at: '2026-09-22T00:00:00Z' }], now, dismissals,
        )).toHaveLength(1)
    })

    it('hands back the whole book object, so the caller can draw and open it', () => {
        const book = { key: 'pair_2', series: 'Axis', index: 2, title: 'Two', coverPath: 'c.jpg' }
        const [row] = computeNextUp(
            [{ key: 'pair_1', series: 'Axis', index: 1 }, book],
            [{ key: 'pair_1', completed: true, at: '2026-09-25T00:00:00Z' }],
            new Date('2026-09-26T00:00:00Z'),
        )

        expect(row.book).toBe(book)
    })

    it('suggests a book once when it is the next book of two series, keeping the first row', () => {
        // A multi-series book is listed once per membership (same key). After the
        // stable sort by last activity, a later row for a key already suggested is
        // dropped, so equal activity leaves the series listed first.
        const books = [
            { key: 'pair_1', series: 'Axis Saga', index: 1 },
            { key: 'pair_1', series: 'Axis Chronological', index: 1 },
            { key: 'pair_2', series: 'Axis Saga', index: 2 },
            { key: 'pair_2', series: 'Axis Chronological', index: 2 },
        ]
        const activity = [{ key: 'pair_1', completed: true, at: '2026-09-25T00:00:00Z' }]
        const now = new Date('2026-09-26T00:00:00Z')

        const rows = computeNextUp(books, activity, now)
        expect(rows.map(r => [r.series, r.key])).toEqual([['Axis Saga', 'pair_2']])

        const flipped = computeNextUp([books[1], books[0], books[3], books[2]], activity, now)
        expect(flipped.map(r => [r.series, r.key])).toEqual([['Axis Chronological', 'pair_2']])
    })

    it('reads naive server timestamps as UTC', () => {
        // user_progress.updated_at arrives without a zone (issue #216). 23:30 UTC
        // on the 90th day is inside the window whatever the runner's zone is.
        const result = computeNextUp(
            [{ key: 'ebook_1', series: 'Axis', index: 1 }, { key: 'ebook_2', series: 'Axis', index: 2 }],
            [{ key: 'ebook_1', completed: true, at: '2026-06-28T23:30:00' }],
            new Date('2026-09-26T00:00:00Z'),
        )

        expect(result.map(r => r.key)).toEqual(['ebook_2'])
    })
})

describe('libraryToNextUpInput', () => {
    const list = (series, index) => (series ? [{ name: series, index }] : [])
    const ebook = (id, series, index, extra = {}) => ({ id, title: `Ebook ${id}`, series_list: list(series, index), cover_path: null, ...extra })
    const audiobook = (id, series, index, extra = {}) => ({ id, title: `Audiobook ${id}`, series_list: list(series, index), cover_path: null, ...extra })

    it('counts a pair once and opens it on its ebook page', () => {
        const e1 = ebook(1, 'Axis', 1, { cover_path: 'e1.jpg' })
        const a2 = audiobook(2, 'Axis', 1)
        const e3 = ebook(3, 'Axis', 2)
        const { books } = libraryToNextUpInput({
            ebooks: [e1, e3], audiobooks: [a2],
            pairs: [{ id: 5, ebook: e1, audiobook: a2 }], progress: [],
        })

        expect(books.map(b => b.key)).toEqual(['pair_5', 'ebook_3'])
        const pair = books[0]
        expect(pair).toMatchObject({ series: 'Axis', index: 1, detailType: 'ebook', detailId: 1, coverPath: 'e1.jpg' })
    })

    it('takes a pair\'s series from the audiobook when the ebook has none', () => {
        const e1 = ebook(1, null, null)
        const a2 = audiobook(2, 'Axis', 4)
        const { books } = libraryToNextUpInput({
            ebooks: [e1], audiobooks: [a2], pairs: [{ id: 5, ebook: e1, audiobook: a2 }], progress: [],
        })

        expect(books[0]).toMatchObject({ key: 'pair_5', series: 'Axis', index: 4 })
    })

    it('lists a book once per series it is in, primary series first, under one key', () => {
        const e1 = ebook(1, null, null, { series_list: [{ name: 'Axis Saga', index: 1 }, { name: 'Axis Chronological', index: 3 }] })
        const e2 = ebook(2, 'Axis Saga', 2)
        const { books } = libraryToNextUpInput({ ebooks: [e1, e2], audiobooks: [], pairs: [], progress: [] })

        expect(books.map(b => [b.key, b.series, b.index])).toEqual([
            ['ebook_1', 'Axis Saga', 1],
            ['ebook_1', 'Axis Chronological', 3],
            ['ebook_2', 'Axis Saga', 2],
        ])
    })

    it('expands a pair over the union of its halves series', () => {
        const e1 = ebook(1, 'Axis Saga', 1)
        const a2 = audiobook(2, null, null, { series_list: [{ name: 'Axis Chronological', index: 3 }, { name: 'axis saga', index: 9 }] })
        const { books } = libraryToNextUpInput({
            ebooks: [e1], audiobooks: [a2], pairs: [{ id: 5, ebook: e1, audiobook: a2 }], progress: [],
        })

        expect(books.map(b => [b.key, b.series, b.index])).toEqual([
            ['pair_5', 'Axis Saga', 1],
            ['pair_5', 'Axis Chronological', 3],
        ])
        expect(books[1]).toMatchObject({ detailType: 'ebook', detailId: 1, title: 'Ebook 1' })
    })

    it('keeps a book with no series as one seriesless entry', () => {
        const { books } = libraryToNextUpInput({ ebooks: [ebook(1, null, null)], audiobooks: [audiobook(2, null, null)], pairs: [], progress: [] })
        expect(books.map(b => [b.key, b.series, b.index])).toEqual([['ebook_1', null, null], ['audiobook_2', null, null]])
    })

    it('files progress on either half of a pair under the pair, preferring captured_at', () => {
        const e1 = ebook(1, 'Axis', 1)
        const a2 = audiobook(2, 'Axis', 1)
        const { activity } = libraryToNextUpInput({
            ebooks: [e1], audiobooks: [a2], pairs: [{ id: 5, ebook: e1, audiobook: a2 }],
            progress: [{
                media_type: 'audiobook', audiobook_id: 2, audio_position_ms: 60_000, is_completed: false,
                updated_at: '2026-09-25T00:00:00', captured_at: '2026-09-20T00:00:00',
            }],
        })

        expect(activity).toEqual([{ key: 'pair_5', completed: false, at: '2026-09-20T00:00:00' }])
    })

    it('ignores a book opened at 0 % and never finished, but keeps a finished one', () => {
        const e1 = ebook(1, 'Axis', 1)
        const e2 = ebook(2, 'Axis', 2)
        const { activity } = libraryToNextUpInput({
            ebooks: [e1, e2], audiobooks: [], pairs: [],
            progress: [
                { media_type: 'ebook', ebook_id: 1, epub_progress_percent: 0, epub_chapter: 0, is_completed: false, updated_at: '2026-09-25T00:00:00' },
                { media_type: 'ebook', ebook_id: 2, epub_progress_percent: 0, is_completed: true, updated_at: '2026-09-25T00:00:00' },
            ],
        })

        expect(activity.map(a => a.key)).toEqual(['ebook_2'])
    })
})
