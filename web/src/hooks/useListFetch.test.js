import { describe, it, expect, vi } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import useListFetch from './useListFetch'

// Issue #276: the loading/error/load() triple every list page hand-rolled,
// with the stale-response guard only useLibraryBrowse had.

describe('useListFetch', () => {
    it('starts loading, then exposes the fetched body', async () => {
        const fetcher = vi.fn(async () => ({ ebooks: [1, 2] }))
        const { result } = renderHook(() => useListFetch(fetcher))

        expect(result.current.loading).toBe(true)
        expect(result.current.data).toBeNull()

        await waitFor(() => expect(result.current.loading).toBe(false))
        expect(result.current.data).toEqual({ ebooks: [1, 2] })
        expect(result.current.error).toBeNull()
        expect(fetcher).toHaveBeenCalledTimes(1)
    })

    it('honours initialData so a page can render before the first response', async () => {
        const fetcher = vi.fn(async () => [7])
        const { result } = renderHook(() => useListFetch(fetcher, { initialData: [] }))

        expect(result.current.data).toEqual([])
        await waitFor(() => expect(result.current.data).toEqual([7]))
    })

    it('surfaces the failure message and stops loading', async () => {
        const fetcher = vi.fn(async () => { throw new Error('server said no') })
        const { result } = renderHook(() => useListFetch(fetcher))

        await waitFor(() => expect(result.current.loading).toBe(false))
        expect(result.current.error).toBe('server said no')
        expect(result.current.data).toBeNull()
    })

    it('falls back to a generic message when the error carries none', async () => {
        const fetcher = vi.fn(async () => { throw new Error('') })
        const { result } = renderHook(() => useListFetch(fetcher))

        await waitFor(() => expect(result.current.error).toBe('Failed to load'))
    })

    it('reload() re-fetches and clears a previous error', async () => {
        const fetcher = vi.fn()
            .mockRejectedValueOnce(new Error('boom'))
            .mockResolvedValueOnce({ ok: true })
        const { result } = renderHook(() => useListFetch(fetcher))

        await waitFor(() => expect(result.current.error).toBe('boom'))

        await act(async () => { await result.current.reload() })

        expect(result.current.error).toBeNull()
        expect(result.current.data).toEqual({ ok: true })
    })

    it('drops a slow response that a newer fetch has already superseded', async () => {
        const resolvers = []
        const fetcher = vi.fn(() => new Promise((resolve) => resolvers.push(resolve)))
        const { result } = renderHook(() => useListFetch(fetcher))

        // A reload starts while the first request is still in flight.
        act(() => { result.current.reload() })
        expect(resolvers).toHaveLength(2)

        // The *first* one lands last. It must not win.
        await act(async () => { resolvers[1]({ generation: 'new' }) })
        await act(async () => { resolvers[0]({ generation: 'old' }) })

        expect(result.current.data).toEqual({ generation: 'new' })
    })

    it('a superseded failure neither sets an error nor clears loading', async () => {
        const rejecters = []
        const resolvers = []
        const fetcher = vi.fn(() => new Promise((resolve, reject) => {
            resolvers.push(resolve)
            rejecters.push(reject)
        }))
        const { result } = renderHook(() => useListFetch(fetcher))

        act(() => { result.current.reload() })
        await act(async () => { resolvers[1]({ ok: true }) })
        await act(async () => { rejecters[0](new Error('stale failure')) })

        expect(result.current.error).toBeNull()
        expect(result.current.data).toEqual({ ok: true })
    })

    it('runs onLoaded after each fresh response, with the body', async () => {
        const onLoaded = vi.fn()
        const fetcher = vi.fn(async () => ({ n: 1 }))
        const { result } = renderHook(() => useListFetch(fetcher, { onLoaded }))

        await waitFor(() => expect(onLoaded).toHaveBeenCalledWith({ n: 1 }))

        await act(async () => { await result.current.reload() })
        expect(onLoaded).toHaveBeenCalledTimes(2)
    })

    it('re-fetches when the fetcher identity changes, and not otherwise', async () => {
        const a = vi.fn(async () => 'a')
        const b = vi.fn(async () => 'b')
        const { result, rerender } = renderHook(({ f }) => useListFetch(f), {
            initialProps: { f: a },
        })
        await waitFor(() => expect(result.current.data).toBe('a'))

        rerender({ f: a })
        expect(a).toHaveBeenCalledTimes(1)

        rerender({ f: b })
        await waitFor(() => expect(result.current.data).toBe('b'))
    })

    it('setData lets a page apply an optimistic edit without a round trip', async () => {
        const fetcher = vi.fn(async () => ({ items: [1] }))
        const { result } = renderHook(() => useListFetch(fetcher))
        await waitFor(() => expect(result.current.loading).toBe(false))

        act(() => { result.current.setData({ items: [1, 2] }) })

        expect(result.current.data).toEqual({ items: [1, 2] })
        expect(fetcher).toHaveBeenCalledTimes(1)
    })
})
