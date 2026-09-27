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

// Two tabs counting at once could each read the store, add their entry and
// write, the second dropping the first's (issue #736). Where the browser has
// Web Locks, the read-modify-write runs under one cross-tab lock; without them
// (or if the lock is refused) it runs directly, as before - the worst case
// there is a recount. Returns a promise that settles once written.
export function writeCounts(key, value) {
    const write = () => writeNow(key, value)
    let locks = null
    try { locks = globalThis.navigator?.locks } catch { locks = null }
    if (!locks?.request) {
        write()
        return Promise.resolve()
    }
    return Promise.resolve()
        .then(() => locks.request(STORAGE_KEY, write))
        .catch(write)
}

function writeNow(key, value) {
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
