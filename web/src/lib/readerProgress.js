/**
 * Reader progress indicator rules (issue #730). One rule with Android's
 * ReaderProgress.kt, held to server/tests/fixtures/sync_parity/reader_progress_cases.json.
 * Pure: no DOM, no storage.
 */
export const PROGRESS_MODES = ['percent', 'pages', 'chapter', 'time']
export const DEFAULT_CHARS_PER_SECOND = 25 // about 250 words a minute at 6 characters a word
const MIN_DWELL_S = 2
const MAX_DWELL_S = 300
const MIN_SAMPLES = 5
const MAX_SAMPLES = 50

export function nextProgressMode(mode) {
    return PROGRESS_MODES[(PROGRESS_MODES.indexOf(mode) + 1) % PROGRESS_MODES.length]
}
export const parseProgressMode = v => (PROGRESS_MODES.includes(v) ? v : 'percent')
export const parsePageMode = v => (v === 'print' ? 'print' : 'ebook')

/** 1-based page within a section from Readium/epub.js progression (page k starts at (k-1)/total). */
export function pageInSection(progression, total) {
    if (!(total >= 1)) return null
    return Math.min(total, Math.max(1, Math.round((progression || 0) * total) + 1))
}

export function ebookPosition(counts, sectionIndex, page) {
    if (!Array.isArray(counts) || sectionIndex < 0 || sectionIndex >= counts.length) return null
    let before = 0
    for (let i = 0; i < sectionIndex; i++) before += counts[i]
    const total = counts.reduce((a, b) => a + b, 0)
    const inSection = Math.min(Math.max(1, page || 1), Math.max(1, counts[sectionIndex]))
    return { page: before + inSection, total }
}

export function resolvePageLabel({ pageMode, fraction, ebook, printList, printPageCount }) {
    if (pageMode === 'print') {
        if (printList && printList.lastLabel) {
            return { current: printList.currentLabel ?? printList.firstLabel, total: printList.lastLabel, kind: 'print' }
        }
        if (printPageCount >= 1) {
            const f = Math.min(1, Math.max(0, fraction || 0))
            const current = Math.min(printPageCount, Math.max(1, Math.ceil(f * printPageCount)))
            return { current: String(current), total: String(printPageCount), kind: 'print' }
        }
    }
    if (ebook) {
        return { current: String(ebook.page), total: String(ebook.total), kind: pageMode === 'print' ? 'ebook-fallback' : 'ebook' }
    }
    return { current: null, total: null, kind: 'pending' }
}

export const formatPageLabel = r => (r.kind === 'pending' ? '…' : `${r.current} of ${r.total}`)
export const formatChapterPage = (page, total) => (page > 0 && total > 0 ? `${page} of ${total} in chapter` : '…')

export function addSpeedSample(samples, charsOnPage, dwellSeconds) {
    if (!(charsOnPage > 0) || !(dwellSeconds >= MIN_DWELL_S) || dwellSeconds > MAX_DWELL_S) return samples
    // samples may come back from localStorage (a later task) as anything JSON.parse produced.
    return [...(Array.isArray(samples) ? samples : []), charsOnPage / dwellSeconds].slice(-MAX_SAMPLES)
}

export function charsPerSecond(samples) {
    if (!Array.isArray(samples) || samples.length < MIN_SAMPLES) return DEFAULT_CHARS_PER_SECOND
    const s = [...samples].sort((a, b) => a - b)
    const m = s.length >> 1
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2
}

export function secondsLeftInSection(sectionChars, page, pages, cps) {
    if (!(sectionChars >= 0) || !(pages >= 1) || !(cps > 0)) return null
    const p = Math.min(Math.max(1, page || 1), pages)
    return (sectionChars * (1 - (p - 1) / pages)) / cps
}

export function formatTimeLeft(seconds) {
    if (seconds == null) return '…'
    if (seconds < 60) return '<1 min left in chapter'
    const mins = Math.ceil(seconds / 60)
    if (mins < 60) return `${mins} min left in chapter`
    return `${Math.floor(mins / 60)} h ${mins % 60} min left in chapter`
}
