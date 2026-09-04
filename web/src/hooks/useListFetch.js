import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * The shared list fetch (issue #276).
 *
 * `useLibraryBrowse` already solved the hard part — a ticket ref so a slow
 * response for a query nobody is looking at any more cannot overwrite the
 * current one — but only LibraryPage used it. Every other list page hand-rolled
 * its own `loading` / `error` / `load()` triple with no such guard, which is
 * five copies of the same code and one of them is always the stale one.
 *
 * This is that triple, once:
 *
 *     const { data, loading, error, reload } = useListFetch(
 *         useCallback(() => getNewItems(), []),
 *     )
 *
 * `fetcher` must be stable (wrap it in `useCallback`) — the hook re-fetches
 * whenever its identity changes, exactly the way `useLibraryBrowse` re-fetches
 * when its query changes.
 *
 * `onLoaded` runs after a *fresh* response lands, for the per-page bookkeeping
 * a reload implies (clearing a selection, collapsing an expanded row). It is
 * read from a ref, so an inline arrow function does not restart the fetch.
 *
 * It deliberately does NOT wrap `useLibraryBrowse`: that hook owns paging,
 * `loadMore`, a span-preserving `reload` and `patchItems`, and pretending the
 * two are one thing would mean carrying page state through every list on the
 * site. They share the staleness guard, and that is all they share.
 */
export default function useListFetch(fetcher, { initialData = null, onLoaded } = {}) {
    const [data, setData] = useState(initialData)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)

    // Every fetch bumps this; a response whose ticket is no longer current
    // belongs to a call nobody is waiting for, so it is dropped.
    const ticketRef = useRef(0)
    const onLoadedRef = useRef(onLoaded)
    onLoadedRef.current = onLoaded

    const run = useCallback(async () => {
        const ticket = ++ticketRef.current
        setLoading(true)
        try {
            const body = await fetcher()
            if (ticket !== ticketRef.current) return
            setData(body)
            setError(null)
            onLoadedRef.current?.(body)
        } catch (e) {
            if (ticket !== ticketRef.current) return
            setError(e.message || 'Failed to load')
        } finally {
            if (ticket === ticketRef.current) setLoading(false)
        }
    }, [fetcher])

    useEffect(() => { run() }, [run])

    return { data, loading, error, reload: run, setData }
}
