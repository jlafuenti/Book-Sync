import { useState, useEffect } from 'react'
import { coverSrc } from '../api'

/**
 * Resolves a cover_path to a token-bearing <img> src. coverSrc() is async
 * (it mints/fetches a short-lived media token), so this hook holds the
 * resolved value in state until the fetch settles.
 */
export default function useCoverSrc(path) {
    const [src, setSrc] = useState(null)

    useEffect(() => {
        let cancelled = false
        if (!path) {
            setSrc(path)
            return
        }
        coverSrc(path).then((resolved) => {
            if (!cancelled) setSrc(resolved)
        }).catch(() => {
            // A token mint can fail (offline, or the server refusing). Without
            // this that was one unhandled rejection per cover — 50 on a library
            // grid (issue #269). There is nothing to show, so fall back to the
            // same empty state a missing cover already renders.
            if (!cancelled) setSrc(null)
        })
        return () => {
            cancelled = true
        }
    }, [path])

    return src
}
