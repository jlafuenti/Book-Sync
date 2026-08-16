import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import useLibraryBrowse from './useLibraryBrowse'

// Issue #120: the hook owns the server-driven Library list — page 1 for a
// query, "load more" appends the next page, a query change resets, stale
// responses are ignored, and a mutation can reload the loaded span in place.

const { getLibraryItemsPageMock } = vi.hoisted(() => ({ getLibraryItemsPageMock: vi.fn() }))
vi.mock('../api', () => ({ getLibraryItemsPage: getLibraryItemsPageMock }))

function page(items, total, pageNo, limit) {
    return { items, total, page: pageNo, limit }
}
const item = (id, kind = 'ebook') => ({ kind, [kind]: { id, title: `Book ${id}` }, pair: null })

beforeEach(() => {
    getLibraryItemsPageMock.mockReset()
})

describe('useLibraryBrowse', () => {
    it('loads page 1 for the query with the page size and exposes items/total/hasMore', async () => {
        getLibraryItemsPageMock.mockResolvedValue(page([item(1), item(2)], 5, 1, 2))
        const { result } = renderHook(() => useLibraryBrowse({ tab: 'all', sort: 'title', dir: 'asc' }, { pageSize: 2 }))

        expect(result.current.loading).toBe(true)
        await waitFor(() => expect(result.current.loading).toBe(false))

        expect(getLibraryItemsPageMock).toHaveBeenCalledWith({ tab: 'all', sort: 'title', dir: 'asc', page: 1, limit: 2 })
        expect(result.current.items.map((i) => i.ebook.id)).toEqual([1, 2])
        expect(result.current.total).toBe(5)
        expect(result.current.hasMore).toBe(true)
    })

    it('loadMore appends the next page and stops at the end', async () => {
        getLibraryItemsPageMock
            .mockResolvedValueOnce(page([item(1), item(2)], 3, 1, 2))
            .mockResolvedValueOnce(page([item(3)], 3, 2, 2))
        const { result } = renderHook(() => useLibraryBrowse({ tab: 'all' }, { pageSize: 2 }))
        await waitFor(() => expect(result.current.loading).toBe(false))

        act(() => { result.current.loadMore() })
        await waitFor(() => expect(result.current.items).toHaveLength(3))

        expect(getLibraryItemsPageMock).toHaveBeenLastCalledWith({ tab: 'all', page: 2, limit: 2 })
        expect(result.current.hasMore).toBe(false)
        // Nothing more to fetch: loadMore is a no-op now.
        act(() => { result.current.loadMore() })
        expect(getLibraryItemsPageMock).toHaveBeenCalledTimes(2)
    })

    it('a query change resets to page 1 of the new query', async () => {
        getLibraryItemsPageMock
            .mockResolvedValueOnce(page([item(1)], 1, 1, 50))
            .mockResolvedValueOnce(page([item(9)], 1, 1, 50))
        const { result, rerender } = renderHook(({ q }) => useLibraryBrowse({ tab: 'all', q }), {
            initialProps: { q: '' },
        })
        await waitFor(() => expect(result.current.items).toHaveLength(1))

        rerender({ q: 'nine' })
        await waitFor(() => expect(result.current.items[0].ebook.id).toBe(9))
        expect(getLibraryItemsPageMock).toHaveBeenLastCalledWith({ tab: 'all', q: 'nine', page: 1, limit: 50 })
    })

    it('ignores a slow response for a query that is no longer current', async () => {
        let resolveFirst
        getLibraryItemsPageMock
            .mockImplementationOnce(() => new Promise((res) => { resolveFirst = res }))
            .mockResolvedValueOnce(page([item(2)], 1, 1, 50))
        const { result, rerender } = renderHook(({ q }) => useLibraryBrowse({ q }), { initialProps: { q: 'a' } })

        rerender({ q: 'b' })
        await waitFor(() => expect(result.current.items.map((i) => i.ebook.id)).toEqual([2]))

        // The stale first response lands late and must not clobber the list.
        await act(async () => { resolveFirst(page([item(1)], 1, 1, 50)) })
        expect(result.current.items.map((i) => i.ebook.id)).toEqual([2])
    })

    it('surfaces an error and clears loading', async () => {
        getLibraryItemsPageMock.mockRejectedValue(new Error('boom'))
        const { result } = renderHook(() => useLibraryBrowse({}))
        await waitFor(() => expect(result.current.loading).toBe(false))
        expect(result.current.error).toBe('boom')
        expect(result.current.items).toEqual([])
    })

    it('reload re-fetches every loaded page so the visible span stays put', async () => {
        getLibraryItemsPageMock
            .mockResolvedValueOnce(page([item(1), item(2)], 4, 1, 2))
            .mockResolvedValueOnce(page([item(3), item(4)], 4, 2, 2))
            // reload: pages 1 and 2 again, one item gone
            .mockResolvedValueOnce(page([item(1), item(3)], 3, 1, 2))
            .mockResolvedValueOnce(page([item(4)], 3, 2, 2))
        const { result } = renderHook(() => useLibraryBrowse({ tab: 'all' }, { pageSize: 2 }))
        await waitFor(() => expect(result.current.loading).toBe(false))
        act(() => { result.current.loadMore() })
        await waitFor(() => expect(result.current.items).toHaveLength(4))

        await act(async () => { await result.current.reload() })

        expect(result.current.items.map((i) => i.ebook.id)).toEqual([1, 3, 4])
        expect(result.current.total).toBe(3)
        expect(getLibraryItemsPageMock).toHaveBeenCalledTimes(4)
    })

    it('patchItems edits the loaded items in place', async () => {
        getLibraryItemsPageMock.mockResolvedValue(page([item(1)], 1, 1, 50))
        const { result } = renderHook(() => useLibraryBrowse({}))
        await waitFor(() => expect(result.current.loading).toBe(false))

        act(() => { result.current.patchItems((items) => items.map((i) => ({ ...i, ebook: { ...i.ebook, title: 'Renamed' } }))) })
        expect(result.current.items[0].ebook.title).toBe('Renamed')
    })
})
