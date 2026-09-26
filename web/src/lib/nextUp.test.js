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
        const result = computeNextUp(testCase.books, testCase.activity, new Date(testCase.now))

        expect(result.map(r => ({ series: r.series, key: r.key }))).toEqual(testCase.expected)
    })
})

describe('computeNextUp', () => {
    it('uses a 90-day window, the owner-chosen value', () => {
        expect(NEXT_UP_WINDOW_DAYS).toBe(90)
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
    const ebook = (id, series, index, extra = {}) => ({ id, title: `Ebook ${id}`, series, series_index: index, cover_path: null, ...extra })
    const audiobook = (id, series, index, extra = {}) => ({ id, title: `Audiobook ${id}`, series, series_index: index, cover_path: null, ...extra })

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
