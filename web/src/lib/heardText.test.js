import { describe, it, expect } from 'vitest'
import { heardTextByPoint } from './heardText'

// Issue #713: the alignment view shows what the transcript heard beside each
// ebook sentence. Sync points carry no heard text of their own, so each cached
// transcript sentence goes to the point whose time range it starts in.

const pt = (id, start_ms, end_ms) => ({ id, start_ms, end_ms })
const heard = (text, start_ms, end_ms = start_ms + 500) => ({ text, start_ms, end_ms })

describe('heardTextByPoint', () => {
    it('gives each sentence to the point whose range it starts in, joining several', () => {
        const points = [pt(1, 0, 2000), pt(2, 2000, 5000)]
        const sentences = [heard('one', 0), heard('two', 1500), heard('three', 2000), heard('four', 4999)]

        const got = heardTextByPoint(points, sentences)

        expect(got.get(1)).toBe('one two')
        expect(got.get(2)).toBe('three four')
    })

    it('leaves out a sentence that starts in a gap between points', () => {
        const points = [pt(1, 0, 1000), pt(2, 3000, 4000)]
        const got = heardTextByPoint(points, [heard('in the gap', 2000), heard('later', 3500)])

        expect(got.get(1)).toBeUndefined()
        expect(got.get(2)).toBe('later')
    })

    it('does not depend on the order the points come in', () => {
        const points = [pt(2, 2000, 5000), pt(1, 0, 2000)]
        const got = heardTextByPoint(points, [heard('a', 100), heard('b', 3000)])

        expect(got.get(1)).toBe('a')
        expect(got.get(2)).toBe('b')
    })

    it('gives nothing to a point with an empty range', () => {
        const points = [pt(1, 0, 2000), pt(2, 2000, 2000), pt(3, 2000, 4000)]
        const got = heardTextByPoint(points, [heard('x', 2000)])

        expect(got.get(2)).toBeUndefined()
        expect(got.get(3)).toBe('x')
    })

    it('handles no points or no sentences', () => {
        expect(heardTextByPoint([], [heard('x', 0)]).size).toBe(0)
        expect(heardTextByPoint([pt(1, 0, 10)], []).size).toBe(0)
        expect(heardTextByPoint([pt(1, 0, 10)], null).size).toBe(0)
    })
})

describe('heardTextByPoint — points sharing a start', () => {
    it('finds the real range among points that start together, whichever comes first', () => {
        const points = [pt(3, 2000, 4000), pt(2, 2000, 2000), pt(1, 0, 2000)]
        const got = heardTextByPoint(points, [heard('x', 2500)])
        expect(got.get(3)).toBe('x')
        expect(got.get(2)).toBeUndefined()
    })
})
