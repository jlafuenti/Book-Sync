import { useCallback, useEffect, useRef, useState } from 'react'
import ePub from 'epubjs'
import { getEbook } from '../api'
import {
    nextProgressMode, parseProgressMode, parsePageMode, ebookPosition, resolvePageLabel,
    formatPageLabel, formatChapterPage, addSpeedSample, charsPerSecond,
    secondsLeftInSection, formatTimeLeft, FALLBACK_NOTICE, FALLBACK_NOTICE_MS,
} from '../lib/readerProgress'
import { countSectionPages, spineSignature } from '../lib/pageCounter'
import { cacheKey, readCounts, writeCounts } from '../lib/pageCountCache'
import { pageListEntries, printListAt, fragmentBeforeOrAt } from '../lib/printPages'

// Device-local, like the font size and reader theme (issue #730).
export const PROGRESS_MODE_KEY = 'tandem_reader_progress_mode'
export const PAGE_MODE_KEY = 'tandem_reader_page_mode'
export const SPEED_SAMPLES_KEY = 'tandem_reader_speed_samples'
const RESIZE_DEBOUNCE_MS = 500
// A fresh page count waits until epub.js has generated its locations (the
// first relocation with a real location index), so the two passes over the
// book do not run at once and stutter the first page turns (issue #736). If
// the locations never come - generation failed - it counts after this anyway.
export const COUNT_FALLBACK_MS = 15000
// `locationsReady`: before `book.locations` has been generated, epub.js
// (0.3.93, rendition.located) reports `start.location` as -1 and
// `start.percentage` as 0 (locationFromCfi returns -1 on an empty list and
// percentageFromLocation(-1) returns 0), so the book-wide fraction is not
// known until `start.location` is a real index (>= 0).
const NO_POSITION = { sectionIndex: -1, page: 0, total: 0, fraction: 0, locationsReady: false }

function readStored(key) {
    try { return localStorage.getItem(key) } catch { return null }
}

function writeStored(key, value) {
    try { localStorage.setItem(key, value) } catch { /* storage unavailable: session-only */ }
}

function loadSamples() {
    try {
        const parsed = JSON.parse(readStored(SPEED_SAMPLES_KEY) || '[]')
        return Array.isArray(parsed) ? parsed : []
    } catch {
        return []
    }
}

/**
 * State and logic behind the reader's progress indicator (issue #730): the
 * tap-to-cycle mode, the ebook/print page setting, the off-screen page count
 * and its cache, the print page count, and the reading-speed samples.
 *
 *     const progress = useReaderProgress({ ebookId, fontSize, ready, viewerRef, bufferRef })
 *     progress.onOpened(book)                      // once the book is open
 *     progress.onRelocated({ location, spineIndex, fraction, book, rendition })
 *
 * `onOpened` and `onRelocated` read only refs, so the `relocated` handler the
 * reader attaches once at open can call them for the life of the book.
 *
 * The count runs against the book `onOpened` was given, with the bytes that
 * were in `bufferRef` at that moment, and only while that book belongs to the
 * current `ebookId` — the reader's `loading` does not flip back when the
 * ebook changes under a mounted reader, so `ready` alone cannot say which
 * book is open.
 */
export default function useReaderProgress({ ebookId, fontSize, ready, viewerRef, bufferRef }) {
    const [mode, setMode] = useState(() => parseProgressMode(readStored(PROGRESS_MODE_KEY)))
    const [pageMode, setPageModeState] = useState(() => parsePageMode(readStored(PAGE_MODE_KEY)))
    const [counts, setCounts] = useState(null)
    const countsRef = useRef(null)
    const [samples, setSamples] = useState(loadSamples)
    const samplesRef = useRef(samples)
    const [position, setPosition] = useState(NO_POSITION)
    const [printList, setPrintList] = useState(null)
    const [printPageCount, setPrintPageCount] = useState(null)
    const [viewerSize, setViewerSize] = useState(null)
    const printEntriesRef = useRef([])
    const [opened, setOpened] = useState(null)
    const ebookIdRef = useRef(ebookId)
    ebookIdRef.current = ebookId
    // The last distinct page the reader was on, and since when: the dwell clock.
    const lastPageRef = useRef(null)

    const applyCounts = useCallback((value) => {
        countsRef.current = value
        setCounts(value)
    }, [])

    // The "no print page count" notice: armed by a tap into pages mode (or
    // choosing print pages there), shown once the page label resolves to the
    // ebook-page fallback, for FALLBACK_NOTICE_MS. Reopening in pages mode
    // never arms it.
    const [noticeArmed, setNoticeArmed] = useState(false)
    const [noticeShown, setNoticeShown] = useState(false)

    const cycle = useCallback(() => {
        const next = nextProgressMode(mode)
        writeStored(PROGRESS_MODE_KEY, next)
        setMode(next)
        setNoticeArmed(next === 'pages')
        setNoticeShown(false)
    }, [mode])

    const setPageMode = useCallback((value) => {
        const next = parsePageMode(value)
        writeStored(PAGE_MODE_KEY, next)
        setPageModeState(next)
        if (next === 'print' && pageMode !== 'print' && mode === 'pages') {
            setNoticeArmed(true)
            setNoticeShown(false)
        }
    }, [mode, pageMode])

    // A different ebook under a mounted reader: drop the previous book's
    // numbers at once, so pages and time read "…" rather than stale values
    // until the new book opens and is counted.
    const shownEbookRef = useRef(ebookId)
    useEffect(() => {
        if (shownEbookRef.current === ebookId) return
        shownEbookRef.current = ebookId
        applyCounts(null)
        setPosition(NO_POSITION)
        setPrintList(null)
        printEntriesRef.current = []
        lastPageRef.current = null
    }, [ebookId, applyCounts])

    // The ebook's print page count, once per ebook. Not every page that opens
    // the reader holds the ebook record, so the reader fetches it itself.
    useEffect(() => {
        let alive = true
        setPrintPageCount(null)
        Promise.resolve()
            .then(() => getEbook(ebookId))
            .then(ebook => { if (alive) setPrintPageCount(ebook?.print_page_count ?? null) })
            .catch(() => { /* no print page count: fall back to ebook pages */ })
        return () => { alive = false }
    }, [ebookId])

    // The viewer's size, re-measured on resize (debounced) once the book is open.
    useEffect(() => {
        const el = viewerRef.current
        if (!ready || !el) return undefined
        const measure = () => {
            const width = Math.round(el.clientWidth)
            const height = Math.round(el.clientHeight)
            if (!(width > 0 && height > 0)) return
            setViewerSize(prev => (prev && prev.width === width && prev.height === height ? prev : { width, height }))
        }
        measure()
        if (typeof ResizeObserver === 'undefined') return undefined
        let timer = null
        const observer = new ResizeObserver(() => {
            clearTimeout(timer)
            timer = setTimeout(measure, RESIZE_DEBOUNCE_MS)
        })
        observer.observe(el)
        return () => {
            clearTimeout(timer)
            observer.disconnect()
        }
    }, [ready, viewerRef])

    // Whether a fresh count may start: epub.js has its locations, or waiting
    // for them has timed out (issue #736). Reset for each opened book.
    const [countMayStart, setCountMayStart] = useState(false)
    useEffect(() => {
        if (!opened) {
            setCountMayStart(false)
            return undefined
        }
        // Locations that already exist (epub.js Locations.length()) need no wait.
        let have = 0
        try { have = opened.book?.locations?.length?.() || 0 } catch { have = 0 }
        setCountMayStart(have > 0)
        if (have > 0) return undefined
        const timer = setTimeout(() => setCountMayStart(true), COUNT_FALLBACK_MS)
        return () => clearTimeout(timer)
    }, [opened])

    // The page count. Starts only once the book has opened, so it never
    // competes with the open, and a fresh count only once epub.js has its
    // locations; a cached one applies at once. A font size or viewer size
    // change aborts it and counts again (or takes the cached result for the
    // new settings).
    useEffect(() => {
        if (!ready || !viewerSize || !opened || opened.ebookId !== ebookId) return undefined
        const { book, buffer } = opened
        if (!buffer) return undefined
        if (!Array.isArray(book.spine?.spineItems) || !book.spine.spineItems.length) return undefined
        const { width, height } = viewerSize
        // The byte length joins the spine signature, so a replaced file with
        // the same spine is counted afresh.
        const signature = `${spineSignature(book)}:${buffer.byteLength}`
        const key = cacheKey({ ebookId, signature, width, height, fontSize })
        const cached = readCounts(key)
        if (cached && Array.isArray(cached.counts) && Array.isArray(cached.chars)) {
            applyCounts(cached)
            return undefined
        }
        applyCounts(null)
        if (!countMayStart) return undefined
        const controller = new AbortController()
        countSectionPages(() => ePub(buffer.slice(0)), { width, height, fontSize, signal: controller.signal })
            .then(result => {
                if (controller.signal.aborted) return
                writeCounts(key, result)
                applyCounts(result)
            })
            .catch(err => {
                if (err?.name === 'AbortError') return
                console.warn('[EbookReader] page count failed:', err?.message || err)
            })
        return () => controller.abort()
    }, [ebookId, fontSize, viewerSize, ready, opened, applyCounts, countMayStart])

    const onOpened = useCallback((book) => {
        const hrefs = (book?.spine?.items || []).map(item => item.href)
        printEntriesRef.current = pageListEntries(book, hrefs)
        lastPageRef.current = null
        setPrintList(null)
        setOpened({ book, buffer: bufferRef.current, ebookId: ebookIdRef.current })
    }, [bufferRef])

    const onRelocated = useCallback(({ location, spineIndex, fraction, book, rendition }) => {
        const page = location.start.displayed?.page || 0
        const total = location.start.displayed?.total || 0
        const locationsReady = location.start.location >= 0
        if (locationsReady) setCountMayStart(true)
        setPosition({ sectionIndex: spineIndex, page, total, fraction, locationsReady })

        const entries = printEntriesRef.current
        if (spineIndex < 0) {
            // Outside the spine: the previous section's print page no longer applies.
            setPrintList(null)
        } else if (entries.length) {
            const contents = rendition?.getContents?.()?.[0]
            const section = book?.spine?.get?.(spineIndex)
            setPrintList(printListAt(entries, spineIndex, frag => {
                try {
                    return fragmentBeforeOrAt(contents, frag, location.start.cfi, section)
                } catch {
                    return false
                }
            }))
        }

        if (spineIndex < 0 || !(page >= 1)) return
        const at = Date.now()
        const prev = lastPageRef.current
        // A re-report of the same page (locations generation, a resize)
        // keeps the dwell clock running.
        if (prev && prev.sectionIndex === spineIndex && prev.page === page) return
        lastPageRef.current = { sectionIndex: spineIndex, page, at }
        if (!prev) return
        const forward = (spineIndex === prev.sectionIndex && page === prev.page + 1)
            || (spineIndex === prev.sectionIndex + 1 && page === 1)
        const counted = countsRef.current
        const pagesInPrev = counted?.counts?.[prev.sectionIndex]
        if (!forward || !(pagesInPrev > 0)) return
        const charsOnPage = counted.chars[prev.sectionIndex] / pagesInPrev
        const next = addSpeedSample(samplesRef.current, charsOnPage, (at - prev.at) / 1000)
        if (next === samplesRef.current) return
        samplesRef.current = next
        setSamples(next)
        writeStored(SPEED_SAMPLES_KEY, JSON.stringify(next))
    }, [])

    // The indicator's text in the page-based modes; percent is the reader's own.
    let text = null
    let fallback = false
    let labelKind = null
    const { sectionIndex, page, total, fraction, locationsReady } = position
    if (mode === 'pages') {
        const ebook = counts ? ebookPosition(counts.counts, sectionIndex, page) : null
        // Print pages scaled from the print page count need the book-wide
        // fraction; before epub.js has its locations that is 0, which would
        // read "1 of N". The embedded page list does not depend on it.
        const scalesFromCount = pageMode === 'print' && !printList?.lastLabel && printPageCount >= 1
        const label = scalesFromCount && !locationsReady
            ? { current: null, total: null, kind: 'pending' }
            : resolvePageLabel({ pageMode, fraction, ebook, printList, printPageCount })
        text = formatPageLabel(label)
        labelKind = label.kind
        fallback = label.kind === 'ebook-fallback'
    } else if (mode === 'chapter') {
        text = formatChapterPage(page, total)
    } else if (mode === 'time') {
        const chars = counts?.chars?.[sectionIndex]
        text = chars == null
            ? '…'
            : formatTimeLeft(secondsLeftInSection(chars, page, total, charsPerSecond(samples)))
    }

    useEffect(() => {
        if (!noticeArmed || labelKind === 'pending') return undefined
        if (labelKind !== 'ebook-fallback') {
            setNoticeArmed(false)
            return undefined
        }
        setNoticeShown(true)
        const timer = setTimeout(() => {
            setNoticeShown(false)
            setNoticeArmed(false)
        }, FALLBACK_NOTICE_MS)
        return () => clearTimeout(timer)
    }, [noticeArmed, labelKind])
    const notice = noticeShown && mode === 'pages' ? FALLBACK_NOTICE : null

    return { mode, cycle, pageMode, setPageMode, text, fallback, notice, onOpened, onRelocated }
}
