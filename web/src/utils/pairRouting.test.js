import { describe, it, expect } from 'vitest'
import { pairTargetPath, pairSourceFromProgress } from './pairRouting'

describe('pairTargetPath', () => {
    it('returns null when there is no pair', () => {
        expect(pairTargetPath(null)).toBe(null)
    })

    it('routes to the format `source` claims when the pair has it', () => {
        expect(pairTargetPath({ audiobook_id: 5 }, 'audiobook')).toBe('/book/audiobook/5')
        expect(pairTargetPath({ ebook_id: 3 }, 'ebook')).toBe('/book/ebook/3')
    })

    it('falls back to ebook, then audiobook, when there is no source', () => {
        expect(pairTargetPath({ ebook_id: 3, audiobook_id: 5 }, null)).toBe('/book/ebook/3')
        expect(pairTargetPath({ audiobook_id: 5 }, null)).toBe('/book/audiobook/5')
    })

    it('falls back when the claimed format has no id on the pair', () => {
        expect(pairTargetPath({ audiobook_id: 5 }, 'ebook')).toBe('/book/audiobook/5')
    })

    it('returns null when neither side exists', () => {
        expect(pairTargetPath({}, 'ebook')).toBe(null)
    })
})

// Issue #215: the source is read off the projection rows, not inferred from
// their timestamps. A pair-scoped write stamps both rows in the same loop, so
// the timestamps this replaced were always equal.
describe('pairSourceFromProgress', () => {
    it('returns null for empty/missing input', () => {
        expect(pairSourceFromProgress(null)).toBe(null)
        expect(pairSourceFromProgress([])).toBe(null)
    })

    it('reads the source the server projected onto the rows', () => {
        const records = [
            { media_type: 'ebook', source: 'audiobook', updated_at: '2026-01-02T00:00:00Z' },
            { media_type: 'audiobook', source: 'audiobook', updated_at: '2026-01-02T00:00:00Z' },
        ]
        expect(pairSourceFromProgress(records)).toBe('audiobook')
    })

    it('ignores rows with no source and takes the first that has one', () => {
        expect(pairSourceFromProgress([
            { media_type: 'ebook' },
            { media_type: 'audiobook', source: 'audiobook' },
        ])).toBe('audiobook')
    })

    it('returns null when no row carries a source (older server)', () => {
        expect(pairSourceFromProgress([
            { media_type: 'ebook', updated_at: '2026-01-03T00:00:00Z' },
        ])).toBe(null)
    })

    it('ignores a value that is neither format', () => {
        expect(pairSourceFromProgress([{ media_type: 'ebook', source: 'unknown' }])).toBe(null)
    })
})
