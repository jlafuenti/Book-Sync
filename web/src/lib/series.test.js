import { describe, it, expect } from 'vitest'
import {
    seriesOf, unionSeries, primarySeries, formatSeriesEntry, formatSeriesList,
    seriesLabel, groupBySeries, seriesNames, hasSeries, seriesIndexIn, cleanSeriesList,
} from './series'

const saga = (index = 4) => ({ name: 'Axis Saga', index })
const chrono = (index = 2) => ({ name: 'Axis Chronological', index })

describe('formatSeriesEntry / formatSeriesList', () => {
    it('writes the index with no trailing zeros', () => {
        expect(formatSeriesEntry({ name: 'Axis Saga', index: 4 })).toBe('Axis Saga #4')
        expect(formatSeriesEntry({ name: 'Axis Saga', index: 4.0 })).toBe('Axis Saga #4')
        expect(formatSeriesEntry({ name: 'Axis Saga', index: 2.5 })).toBe('Axis Saga #2.5')
    })

    it('keeps an index of zero', () => {
        expect(formatSeriesEntry({ name: 'Axis Saga', index: 0 })).toBe('Axis Saga #0')
    })

    it('writes a bare name when there is no index', () => {
        expect(formatSeriesEntry({ name: 'Axis Saga', index: null })).toBe('Axis Saga')
        expect(formatSeriesEntry({ name: 'Axis Saga' })).toBe('Axis Saga')
    })

    it('joins the entries with "; " and gives "" for an empty list', () => {
        expect(formatSeriesList([saga(), { name: 'Other', index: null }])).toBe('Axis Saga #4; Other')
        expect(formatSeriesList([])).toBe('')
        expect(formatSeriesList(null)).toBe('')
        expect(formatSeriesList(undefined)).toBe('')
    })
})

describe('primarySeries', () => {
    it('is entry 0, or null for an empty list', () => {
        expect(primarySeries([saga(), chrono()])).toEqual(saga())
        expect(primarySeries([])).toBeNull()
        expect(primarySeries(null)).toBeNull()
    })
})

describe('seriesLabel', () => {
    it('shows the primary series alone when there is one', () => {
        expect(seriesLabel([saga()])).toBe('Axis Saga #4')
    })

    it('adds +N for the others', () => {
        expect(seriesLabel([saga(), chrono()])).toBe('Axis Saga #4 +1')
        expect(seriesLabel([saga(), chrono(), { name: 'Third', index: 1 }])).toBe('Axis Saga #4 +2')
    })

    it('is "" with no series', () => {
        expect(seriesLabel([])).toBe('')
        expect(seriesLabel(undefined)).toBe('')
    })
})

describe('unionSeries / seriesOf', () => {
    it('lists the ebook entries first, then audiobook names it lacks', () => {
        const eb = [saga(4)]
        const ab = [chrono(7), { name: 'axis saga', index: 9 }]
        expect(unionSeries(eb, ab)).toEqual([saga(4), chrono(7)])
    })

    it('the ebook index wins even when null', () => {
        expect(unionSeries([{ name: 'Axis Saga', index: null }], [saga(4)]))
            .toEqual([{ name: 'Axis Saga', index: null }])
    })

    it('copes with missing lists', () => {
        expect(unionSeries(undefined, [saga()])).toEqual([saga()])
        expect(unionSeries([saga()], null)).toEqual([saga()])
        expect(unionSeries(null, null)).toEqual([])
    })

    it('seriesOf takes a pair-shaped object', () => {
        const pair = { ebook: { series_list: [saga()] }, audiobook: { series_list: [chrono()] } }
        expect(seriesOf(pair)).toEqual([saga(), chrono()])
        expect(seriesOf({ ebook: null, audiobook: { series_list: [chrono()] } })).toEqual([chrono()])
    })

    it('seriesOf takes a single medium or a display entry', () => {
        expect(seriesOf({ series_list: [saga()] })).toEqual([saga()])
        expect(seriesOf({})).toEqual([])
        expect(seriesOf(null)).toEqual([])
    })
})

describe('seriesNames / hasSeries / seriesIndexIn', () => {
    it('lists the names in order', () => {
        expect(seriesNames([saga(), chrono()])).toEqual(['Axis Saga', 'Axis Chronological'])
        expect(seriesNames(null)).toEqual([])
    })

    it('hasSeries tests membership case-insensitively', () => {
        expect(hasSeries([saga()], 'axis saga')).toBe(true)
        expect(hasSeries([saga()], 'Other')).toBe(false)
        expect(hasSeries([], 'Axis Saga')).toBe(false)
    })

    it('seriesIndexIn finds the index in one series', () => {
        expect(seriesIndexIn([saga(4), chrono(2)], 'Axis Chronological')).toBe(2)
        expect(seriesIndexIn([saga(4)], 'Other')).toBeNull()
        expect(seriesIndexIn([{ name: 'Axis Saga', index: null }], 'Axis Saga')).toBeNull()
    })
})

describe('cleanSeriesList', () => {
    it('trims names, parses numbers and drops blank names', () => {
        expect(cleanSeriesList([
            { name: '  Axis Saga ', index: '4' },
            { name: '   ', index: '9' },
            { name: 'Axis Chronological', index: '2.5' },
            { name: 'Bare', index: '' },
            { name: 'Nulled', index: null },
            { name: 'Numeric', index: 3 },
        ])).toEqual([
            { name: 'Axis Saga', index: 4 },
            { name: 'Axis Chronological', index: 2.5 },
            { name: 'Bare', index: null },
            { name: 'Nulled', index: null },
            { name: 'Numeric', index: 3 },
        ])
    })

    it('keeps the first of a repeated name, whatever its case', () => {
        expect(cleanSeriesList([{ name: 'Axis Saga', index: 1 }, { name: 'axis saga', index: 2 }]))
            .toEqual([{ name: 'Axis Saga', index: 1 }])
    })

    it('gives null for an index that is not a number, and [] for nothing', () => {
        expect(cleanSeriesList([{ name: 'A', index: 'x' }])).toEqual([{ name: 'A', index: null }])
        expect(cleanSeriesList(undefined)).toEqual([])
        expect(cleanSeriesList([])).toEqual([])
    })
})

describe('groupBySeries', () => {
    const a = { id: 'a', list: [saga(1), chrono(3)] }
    const b = { id: 'b', list: [chrono(1)] }
    const c = { id: 'c', list: [] }
    const d = { id: 'd', list: [{ name: 'AXIS SAGA', index: 2 }] }

    it('puts an item in every group it belongs to, with that series index', () => {
        const groups = groupBySeries([a, b, c, d], x => x.list)
        expect(groups.map(g => g.name)).toEqual(['Axis Saga', 'Axis Chronological'])
        expect(groups[0].entries).toEqual([{ item: a, index: 1 }, { item: d, index: 2 }])
        expect(groups[1].entries).toEqual([{ item: a, index: 3 }, { item: b, index: 1 }])
    })

    it('leaves out items with no series', () => {
        expect(groupBySeries([c], x => x.list)).toEqual([])
    })

    it('groups a name once when an item lists it twice', () => {
        const dup = { id: 'x', list: [saga(1), { name: 'axis saga', index: 5 }] }
        const groups = groupBySeries([dup], x => x.list)
        expect(groups).toHaveLength(1)
        expect(groups[0].entries).toEqual([{ item: dup, index: 1 }])
    })
})
