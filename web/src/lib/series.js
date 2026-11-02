/**
 * Series helpers (issue #907).
 *
 * A book belongs to a *list* of series: `series_list: [{name, index}]` on every
 * ebook and audiobook the API returns, entry 0 being the primary series. These
 * helpers are the one place the web reads that list, so the pair rule, the
 * canonical text and the "+N" label cannot drift between pages.
 */

const fold = (name) => String(name ?? '').trim().toLowerCase()

/** The entries of a list, tolerating a missing one. */
function entries(list) {
    return Array.isArray(list) ? list.filter(e => e && e.name) : []
}

/**
 * A pair's series: the ebook's entries first, then the audiobook's names the
 * ebook lacks (case-insensitive), the ebook's index winning on a shared name.
 */
export function unionSeries(ebookList, audiobookList) {
    const out = []
    const seen = new Set()
    for (const e of [...entries(ebookList), ...entries(audiobookList)]) {
        const key = fold(e.name)
        if (seen.has(key)) continue
        seen.add(key)
        out.push(e)
    }
    return out
}

/**
 * The series of one thing the pages show: a pair-shaped `{ebook, audiobook}`
 * (union rule), or a single medium / display entry carrying `series_list`.
 */
export function seriesOf(entry) {
    if (!entry) return []
    if ('ebook' in entry || 'audiobook' in entry) {
        return unionSeries(entry.ebook?.series_list, entry.audiobook?.series_list)
    }
    return entries(entry.series_list)
}

/** Entry 0, or null when the book has no series. */
export function primarySeries(list) {
    return entries(list)[0] ?? null
}

/** `Name #4`, `Name #2.5`, or the bare name when there is no index. */
export function formatSeriesEntry(entry) {
    const idx = entry.index
    return idx != null && idx !== '' ? `${entry.name} #${Number(idx)}` : entry.name
}

/** The canonical string, `Name #4; Other #2`. Empty list gives "". */
export function formatSeriesList(list) {
    return entries(list).map(formatSeriesEntry).join('; ')
}

/** Card/row text: the primary series, plus ` +N` for the others. */
export function seriesLabel(list) {
    const all = entries(list)
    if (!all.length) return ''
    const extra = all.length > 1 ? ` +${all.length - 1}` : ''
    return `${formatSeriesEntry(all[0])}${extra}`
}

/** Every series name, in order. */
export function seriesNames(list) {
    return entries(list).map(e => e.name)
}

/** Whether the list has a series of this name (case-insensitive). */
export function hasSeries(list, name) {
    const key = fold(name)
    return entries(list).some(e => fold(e.name) === key)
}

/** The book number in the named series, or null. */
export function seriesIndexIn(list, name) {
    const key = fold(name)
    const hit = entries(list).find(e => fold(e.name) === key)
    return hit?.index ?? null
}

/**
 * Form state to request body: trimmed names, numeric (or null) indexes, blank
 * names dropped, a repeated name (any case) kept once, first wins. The editor
 * keeps the number as typed, so this is where it becomes a number.
 */
export function cleanSeriesList(list) {
    const out = []
    const seen = new Set()
    for (const e of Array.isArray(list) ? list : []) {
        const name = String(e?.name ?? '').trim()
        if (!name || seen.has(fold(name))) continue
        seen.add(fold(name))
        const n = e.index === null || e.index === undefined || e.index === '' ? NaN : Number(e.index)
        out.push({ name, index: Number.isFinite(n) ? n : null })
    }
    return out
}

/**
 * Group items by series. An item lands in every group it belongs to, carrying
 * that series' index: `[{name, entries: [{item, index}]}]`, in order of first
 * appearance. Items with no series are left out. Names group case-insensitively
 * and the first spelling seen is the display name.
 */
export function groupBySeries(items, getList) {
    const groups = new Map()
    for (const item of items) {
        const seen = new Set()
        for (const e of entries(getList(item))) {
            const key = fold(e.name)
            if (seen.has(key)) continue
            seen.add(key)
            if (!groups.has(key)) groups.set(key, { name: e.name, entries: [] })
            groups.get(key).entries.push({ item, index: e.index ?? null })
        }
    }
    return [...groups.values()]
}
