import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import useKnownSeries from './useKnownSeries'

const { getLibraryFacetsMock } = vi.hoisted(() => ({ getLibraryFacetsMock: vi.fn() }))
vi.mock('../api', () => ({ getLibraryFacets: getLibraryFacetsMock }))

// Issue #907: the series names already in the library, for the type-ahead.

beforeEach(() => { getLibraryFacetsMock.mockReset() })

describe('useKnownSeries', () => {
    it('starts empty, then yields the library\'s series names sorted', async () => {
        getLibraryFacetsMock.mockResolvedValue({
            series: [{ name: 'Axis Saga', count: 3 }, { name: 'axis chronological', count: 1 }, { name: 'Bartleby Cycle', count: 2 }],
        })
        const { result } = renderHook(() => useKnownSeries())
        expect(result.current).toEqual([])

        await waitFor(() => expect(result.current).toEqual(['axis chronological', 'Axis Saga', 'Bartleby Cycle']))
        expect(getLibraryFacetsMock).toHaveBeenCalledWith({ tab: 'all' })
    })

    it('stays empty when the facets cannot be loaded', async () => {
        getLibraryFacetsMock.mockRejectedValue(new Error('facets are down'))
        const { result } = renderHook(() => useKnownSeries())

        await waitFor(() => expect(getLibraryFacetsMock).toHaveBeenCalled())
        expect(result.current).toEqual([])
    })

    it('stays empty when the answer has no series', async () => {
        getLibraryFacetsMock.mockResolvedValue({})
        const { result } = renderHook(() => useKnownSeries())

        await waitFor(() => expect(getLibraryFacetsMock).toHaveBeenCalled())
        expect(result.current).toEqual([])
    })

    it('ignores an answer that arrives after unmount', async () => {
        let resolve
        getLibraryFacetsMock.mockReturnValue(new Promise(r => { resolve = r }))
        const { result, unmount } = renderHook(() => useKnownSeries())
        unmount()

        resolve({ series: [{ name: 'Axis Saga', count: 1 }] })
        await Promise.resolve()

        expect(result.current).toEqual([])
    })

    it('does not fetch while disabled, and does once enabled', async () => {
        getLibraryFacetsMock.mockResolvedValue({ series: [{ name: 'Axis Saga', count: 1 }] })
        const { result, rerender } = renderHook(({ on }) => useKnownSeries(on), { initialProps: { on: false } })

        expect(getLibraryFacetsMock).not.toHaveBeenCalled()
        expect(result.current).toEqual([])

        rerender({ on: true })
        await waitFor(() => expect(result.current).toEqual(['Axis Saga']))
        expect(getLibraryFacetsMock).toHaveBeenCalledTimes(1)
    })

    it('survives an api module without getLibraryFacets', async () => {
        getLibraryFacetsMock.mockImplementation(() => { throw new Error('not a function') })
        const { result } = renderHook(() => useKnownSeries())
        await waitFor(() => expect(getLibraryFacetsMock).toHaveBeenCalled())
        expect(result.current).toEqual([])
    })
})
