// Per-device cache of page counts (issue #730): counting every spine section
// takes real layout work (~65 ms/section, `pageCounter.js`), so once a device
// has counted a book at a given viewport/font size it keeps the result in
// localStorage rather than recounting on every reader open. Keyed on every
// input that changes the page counts, so a resize or font-size change misses
// the cache instead of returning stale counts.

const STORAGE_KEY = 'tandem_page_counts_v1'
const MAX_ENTRIES = 20

export function cacheKey({ ebookId, signature, width, height, fontSize }) {
    return `${ebookId}:${signature}:${width}x${height}:${fontSize}`
}

function readStore() {
    try {
        const raw = localStorage.getItem(STORAGE_KEY)
        if (!raw) return {}
        const parsed = JSON.parse(raw)
        return parsed && typeof parsed === 'object' ? parsed : {}
    } catch {
        return {}
    }
}

export function readCounts(key) {
    try {
        const entry = readStore()[key]
        return entry ? entry.v : null
    } catch {
        return null
    }
}

export function writeCounts(key, value) {
    try {
        const store = readStore()
        // Delete before reinserting so an existing key moves to the end —
        // object key order is insertion order, and that order is what
        // eviction below treats as oldest-to-newest.
        if (key in store) delete store[key]
        store[key] = { v: value, t: Date.now() }
        const keys = Object.keys(store)
        while (keys.length > MAX_ENTRIES) {
            delete store[keys.shift()]
        }
        localStorage.setItem(STORAGE_KEY, JSON.stringify(store))
    } catch {
        // Storage disabled, full, or otherwise unavailable — counting just
        // runs again next time, which is correct, only slower.
    }
}
