import { describe, it, expect } from 'vitest'
import { pairTargetPath, lastFormatFromProgress } from './pairRouting'

describe('pairTargetPath', () => {
    it('returns null when there is no pair', () => {
        expect(pairTargetPath(null)).toBe(null)
    })

    it('routes to the last-used format when it exists', () => {
        expect(pairTargetPath({ audiobook_id: 5 }, 'audiobook')).toBe('/book/audiobook/5')
        expect(pairTargetPath({ ebook_id: 3 }, 'ebook')).toBe('/book/ebook/3')
    })

    it('falls back to ebook, then audiobook, when lastFormat is unknown', () => {
        expect(pairTargetPath({ ebook_id: 3, audiobook_id: 5 }, null)).toBe('/book/ebook/3')
        expect(pairTargetPath({ audiobook_id: 5 }, null)).toBe('/book/audiobook/5')
    })

    it('falls back when the last-used format has no id on the pair', () => {
        expect(pairTargetPath({ audiobook_id: 5 }, 'ebook')).toBe('/book/audiobook/5')
    })

    it('returns null when neither side exists', () => {
        expect(pairTargetPath({}, 'ebook')).toBe(null)
    })
})

describe('lastFormatFromProgress', () => {
    it('returns null for empty/missing input', () => {
        expect(lastFormatFromProgress(null)).toBe(null)
        expect(lastFormatFromProgress([])).toBe(null)
    })

    it('picks the more recently updated format', () => {
        const records = [
            { media_type: 'ebook', updated_at: '2026-01-01T00:00:00Z' },
            { media_type: 'audiobook', updated_at: '2026-01-02T00:00:00Z' },
        ]
        expect(lastFormatFromProgress(records)).toBe('audiobook')
    })

    it('returns the only format present', () => {
        expect(lastFormatFromProgress([
            { media_type: 'ebook', updated_at: '2026-01-03T00:00:00Z' },
        ])).toBe('ebook')
    })
})
