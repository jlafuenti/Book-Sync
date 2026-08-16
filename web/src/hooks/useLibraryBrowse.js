import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { getLibraryItemsPage } from '../api'

/**
 * Server-driven Library list (issue #120).
 *
 * Owns the paged `GET /api/library/items` result for one query
 * (`{tab, kind, q, author, series, sort, dir}`): page 1 loads when the query
 * changes, `loadMore()` appends the next page, `reload()` re-fetches every
 * page currently loaded (so a delete/edit refresh keeps the visible span
 * instead of snapping back to the top), and `patchItems()` applies an
 * optimistic edit. Responses for a query that is no longer current are
 * dropped, so a slow page-1 for "du" can't overwrite the results for "dune".
 */
const DEFAULT_PAGE_SIZE = 50

// Strip empty filters so `{q: ''}` and `{}` are the same query (and the same
// request); the API helper omits empties anyway.
function normalize(query) {
    const out = {}
    for (const [k, v] of Object.entries(query || {})) {
        if (v !== undefined && v !== null && v !== '') out[k] = v
    }
    return out
}

export default function useLibraryBrowse(query, { pageSize = DEFAULT_PAGE_SIZE } = {}) {
    const key = JSON.stringify(normalize(query))
    const normalized = useMemo(() => JSON.parse(key), [key])

    const [items, setItems] = useState([])
    const [total, setTotal] = useState(0)
    const [page, setPage] = useState(0)
    const [loading, setLoading] = useState(true)
    const [loadingMore, setLoadingMore] = useState(false)
    const [error, setError] = useState('')

    // Every fetch bumps this; a response whose ticket is no longer current is
    // stale and ignored.
    const ticketRef = useRef(0)
    const pageRef = useRef(0)
    const busyRef = useRef(false)

    const fetchOne = useCallback((p) => getLibraryItemsPage({ ...normalized, page: p, limit: pageSize }), [normalized, pageSize])

    const loadFirst = useCallback(async () => {
        const ticket = ++ticketRef.current
        busyRef.current = true
        setLoading(true)
        setError('')
        try {
            const body = await fetchOne(1)
            if (ticket !== ticketRef.current) return
            setItems(body.items || [])
            setTotal(body.total || 0)
            pageRef.current = 1
            setPage(1)
        } catch (err) {
            if (ticket !== ticketRef.current) return
            setError(err.message || 'Failed to load library')
        } finally {
            if (ticket === ticketRef.current) {
                busyRef.current = false
                setLoading(false)
            }
        }
    }, [fetchOne])

    useEffect(() => { loadFirst() }, [loadFirst])

    const hasMore = items.length < total

    const loadMore = useCallback(async () => {
        if (busyRef.current || !hasMore) return
        const ticket = ticketRef.current
        const next = pageRef.current + 1
        busyRef.current = true
        setLoadingMore(true)
        try {
            const body = await fetchOne(next)
            if (ticket !== ticketRef.current) return
            setItems((prev) => [...prev, ...(body.items || [])])
            setTotal(body.total || 0)
            pageRef.current = next
            setPage(next)
        } catch (err) {
            if (ticket !== ticketRef.current) return
            setError(err.message || 'Failed to load more')
        } finally {
            if (ticket === ticketRef.current) {
                busyRef.current = false
                setLoadingMore(false)
            }
        }
    }, [fetchOne, hasMore])

    const reload = useCallback(async () => {
        const ticket = ++ticketRef.current
        const pages = Math.max(pageRef.current, 1)
        busyRef.current = true
        setError('')
        try {
            const bodies = await Promise.all(Array.from({ length: pages }, (_, i) => fetchOne(i + 1)))
            if (ticket !== ticketRef.current) return
            setItems(bodies.flatMap((b) => b.items || []))
            setTotal(bodies[bodies.length - 1]?.total || 0)
            pageRef.current = pages
            setPage(pages)
        } catch (err) {
            if (ticket !== ticketRef.current) return
            setError(err.message || 'Failed to reload library')
        } finally {
            if (ticket === ticketRef.current) {
                busyRef.current = false
                setLoading(false)
            }
        }
    }, [fetchOne])

    const patchItems = useCallback((updater) => setItems((prev) => updater(prev)), [])

    return { items, total, page, loading, loadingMore, error, hasMore, loadMore, reload, patchItems }
}
