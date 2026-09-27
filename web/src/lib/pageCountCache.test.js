import { describe, it, expect, beforeEach } from 'vitest'
import { cacheKey, readCounts, writeCounts } from './pageCountCache'

const STORAGE_KEY = 'tandem_page_counts_v1'

describe('pageCountCache (issue #730)', () => {
    beforeEach(() => {
        localStorage.clear()
    })

    it('cacheKey includes every field', () => {
        const a = cacheKey({ ebookId: 1, signature: 'sig', width: 800, height: 600, fontSize: 100 })
        // Changing any single field must change the key.
        expect(cacheKey({ ebookId: 2, signature: 'sig', width: 800, height: 600, fontSize: 100 })).not.toBe(a)
        expect(cacheKey({ ebookId: 1, signature: 'other', width: 800, height: 600, fontSize: 100 })).not.toBe(a)
        expect(cacheKey({ ebookId: 1, signature: 'sig', width: 801, height: 600, fontSize: 100 })).not.toBe(a)
        expect(cacheKey({ ebookId: 1, signature: 'sig', width: 800, height: 601, fontSize: 100 })).not.toBe(a)
        expect(cacheKey({ ebookId: 1, signature: 'sig', width: 800, height: 600, fontSize: 101 })).not.toBe(a)
        // Same fields produce the same key.
        expect(cacheKey({ ebookId: 1, signature: 'sig', width: 800, height: 600, fontSize: 100 })).toBe(a)
    })

    it('round-trips a value through write and read', () => {
        const key = cacheKey({ ebookId: 1, signature: 'sig', width: 800, height: 600, fontSize: 100 })
        expect(readCounts(key)).toBeNull()
        writeCounts(key, { counts: [3, 0, 5], chars: [120, 0, 250] })
        expect(readCounts(key)).toEqual({ counts: [3, 0, 5], chars: [120, 0, 250] })
    })

    it('keeps only the 20 most recently written keys, evicting the oldest', () => {
        for (let i = 0; i < 25; i++) {
            writeCounts(`key-${i}`, { counts: [i], chars: [i] })
        }
        // The 5 oldest (0-4) are evicted; the 20 newest (5-24) remain.
        for (let i = 0; i < 5; i++) expect(readCounts(`key-${i}`)).toBeNull()
        for (let i = 5; i < 25; i++) expect(readCounts(`key-${i}`)).toEqual({ counts: [i], chars: [i] })

        const raw = JSON.parse(localStorage.getItem(STORAGE_KEY))
        expect(Object.keys(raw)).toHaveLength(20)
    })

    it('re-writing an existing key refreshes its recency instead of duplicating it', () => {
        for (let i = 0; i < 20; i++) writeCounts(`key-${i}`, { counts: [i], chars: [i] })
        // Touch key-0 again so it is now the newest, then push 5 more new keys in.
        writeCounts('key-0', { counts: [999], chars: [999] })
        for (let i = 20; i < 25; i++) writeCounts(`key-${i}`, { counts: [i], chars: [i] })
        // key-0 should have survived the eviction because it was refreshed.
        expect(readCounts('key-0')).toEqual({ counts: [999], chars: [999] })
    })

    it('readCounts returns null when localStorage.getItem throws', () => {
        const original = localStorage.getItem
        localStorage.getItem = () => { throw new Error('storage disabled') }
        try {
            expect(readCounts('any-key')).toBeNull()
        } finally {
            localStorage.getItem = original
        }
    })

    it('readCounts returns null for malformed JSON without throwing', () => {
        localStorage.setItem(STORAGE_KEY, '{not valid json')
        expect(() => readCounts('any-key')).not.toThrow()
        expect(readCounts('any-key')).toBeNull()
    })

    it('writeCounts swallows a localStorage.setItem failure without throwing', () => {
        const original = localStorage.setItem
        localStorage.setItem = () => { throw new Error('quota exceeded') }
        try {
            expect(() => writeCounts('any-key', { counts: [1], chars: [1] })).not.toThrow()
        } finally {
            localStorage.setItem = original
        }
    })
})
