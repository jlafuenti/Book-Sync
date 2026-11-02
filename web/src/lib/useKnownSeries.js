import { useEffect, useState } from 'react'
import { getLibraryFacets } from '../api'

/**
 * The series names already in the library, sorted, for the series type-ahead
 * (issue #907). Starts as `[]` and stays `[]` if the facets cannot be loaded:
 * suggestions are a convenience, never a reason to block an edit. Pass
 * `enabled = false` to skip the request (a modal that is not open yet).
 */
export default function useKnownSeries(enabled = true) {
    const [names, setNames] = useState([])

    useEffect(() => {
        if (!enabled) return undefined
        let cancelled = false
        ;(async () => {
            try {
                const facets = await getLibraryFacets({ tab: 'all' })
                if (cancelled) return
                setNames(
                    (facets?.series || [])
                        .map(s => s.name)
                        .sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' })),
                )
            } catch { /* no suggestions */ }
        })()
        return () => { cancelled = true }
    }, [enabled])

    return names
}
