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
        })
        return () => {
            cancelled = true
        }
    }, [path])

    return src
}
