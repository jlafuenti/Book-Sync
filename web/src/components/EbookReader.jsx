import React, { useEffect, useRef, useState, useCallback } from 'react'
import { matchTextToAudio } from '../api'
// One module owns the contract's write rules (issue #274): scope, the device
// triple, who may claim `source`, and what counts as a conflict.
import {
    positionTarget, writePosition, keepalivePosition, conflictFrom,
} from '../lib/position'
// The restore ladder's execution, the landing verdict and the write gate
// (issue #278) — the piece docs/position-sync-contract.md cares about.
import { executeRestore, restorePosition, createWriteGate } from '../lib/restoreController'
import {
    normalizeForSearch, extractSearchableText, WHITESPACE_VARIANT_CHAR_RE,
} from '../lib/textSearch'
import { getReaderPalette, READER_MODES, DEFAULT_THEME } from '../themes'
import useEpubRendition, { paletteCss } from '../hooks/useEpubRendition'
import { useTheme } from '../ThemeContext'
import './EbookReader.css'

// Reader display prefs persist across sessions (issue #57) — device-local,
// like Android's reader_display SharedPreferences.
const FONT_SIZE_KEY = 'tandem_reader_font_size'
const READER_THEME_KEY = 'tandem_reader_theme'
const MIN_FONT = 60
const MAX_FONT = 200

function loadStoredFontSize() {
    const stored = parseInt(localStorage.getItem(FONT_SIZE_KEY), 10)
    if (Number.isFinite(stored)) return Math.min(MAX_FONT, Math.max(MIN_FONT, stored))
    return 100
}

function loadStoredReaderTheme() {
    const stored = localStorage.getItem(READER_THEME_KEY)
    return READER_MODES.includes(stored) ? stored : 'match'
}

const READER_MODE_LABELS = [
    ['match', 'Match app'],
    ['light', 'Light'],
    ['sepia', 'Sepia'],
    ['dark', 'Dark'],
]

// Re-exported for the existing unit tests; it lives in lib/restoreController now.
export { executeRestore }

function EbookReader({ ebookId, pairId, initialChapter, initialTextPreview, onClose, bookTitle, onSwitchToAudio, saveFlushRef }) {
    const viewerRef = useRef(null)
    const saveTimerRef = useRef(null)

    const [showToc, setShowToc] = useState(false)
    const [currentCfi, setCurrentCfi] = useState(null)
    const [progressPercent, setProgressPercent] = useState(0)
    const [currentChapter, setCurrentChapter] = useState('')
    const [fontSize, setFontSize] = useState(loadStoredFontSize)
    const fontSizeRef = useRef(fontSize)
    const [readerTheme, setReaderTheme] = useState(loadStoredReaderTheme)
    const [showThemeMenu, setShowThemeMenu] = useState(false)
    // The reader may be mounted without a ThemeProvider (tests, embeds) —
    // fall back to the default app theme then.
    const themeCtx = useTheme()
    const appTheme = themeCtx?.theme || DEFAULT_THEME
    const palette = getReaderPalette(readerTheme, appTheme)
    // Ref mirror for the content hook, which is registered once inside the
    // [ebookId]-only load effect and must always read the current palette.
    const paletteRef = useRef(palette)
    paletteRef.current = palette
    // The epub.js lifecycle (issue #278). `handleOpened` below runs the
    // restore ladder and attaches the `relocated` handler in the same async
    // continuation, between the TOC load and the spinner clearing;
    // `handleTeardown` flushes the pending save before the book is destroyed.
    const { bookRef, renditionRef, toc, loading, error } = useEpubRendition(
        viewerRef, ebookId,
        { fontSizeRef, paletteRef, onOpened: handleOpened, onTeardown: handleTeardown },
    )
    // Save-button feedback: 'saved' shows the ✓, 'error' shows "Not saved".
    // The ✓ only ever means a write actually landed (issue #159) — it used to
    // flash success even while the gate silently swallowed every save.
    const [saveState, setSaveState] = useState(null)
    // Stale-conflict affordance (issue #54): set when the progress write in
    // doSave() comes back `rejected: true` with a newer position from a
    // genuinely different device. { cfi, deviceName }. Only cleared via the
    // explicit "Jump" click below -- never auto-navigated.
    //
    // A rejection whose authoritative record carries no epub.js CFI hint sets
    // this same state with `cfi: null` -- there's no jump target then, only a
    // passive "Dismiss"-only notice. Never overwrites an already-showing
    // jump-capable (cfi-bearing) conflict with a lesser message-only one.
    const [staleConflict, setStaleConflict] = useState(null)
    const currentSpineIndexRef = useRef(initialChapter ?? 0)
    // Tracks progression (0-1) within the current chapter, updated on each page turn
    const currentChapterProgressionRef = useRef(0)
    // Whether we've done the initial text-based navigation (only do it once)
    const textNavDoneRef = useRef(false)
    // Suppress auto-saves while text nav is hopping between chapters
    const textNavInProgressRef = useRef(false)
    // Nothing may be saved until the restore has landed (or the record was
    // genuinely empty). Without this gate a failed restore sitting on page one
    // gets persisted over a real position from another device. The gate's
    // flags (`positionEstablished`, `restoreLanded`, `audioConfirmPending`,
    // `userNavigated`) and their transitions are documented with
    // `createWriteGate`; it lives in a ref so the mount-time `relocated`
    // handler and every save path read the same object.
    const gateRef = useRef(null)
    if (!gateRef.current) gateRef.current = createWriteGate()
    const gate = gateRef.current
    // The canonical record this reader opened with.
    const positionRef = useRef(null)
    // True when the record held a position we could not resolve. Distinct from
    // "unread": saving stays blocked and the reader is told.
    const [unresolvedPosition, setUnresolvedPosition] = useState(false)
    // Latest relocated position, readable from cleanup/lifecycle handlers that
    // would otherwise close over stale state (issue #158).
    const latestPositionRef = useRef({ cfi: null, percent: 0, spineIndex: initialChapter ?? 0 })
    // CFI of the last position write actually issued (debounced save, manual
    // save, unmount flush or lifecycle keepalive). Flushes skip when the
    // position hasn't moved since — visibilitychange fires on every tab
    // switch, and a duplicate write buys nothing.
    const lastIssuedSaveRef = useRef(null)

    // The text-nav pass confirmed an audio-derived landing: the preview text
    // was actually located in this book.
    const confirmProvisionalLanding = useCallback(() => {
        if (gate.confirmProvisionalLanding()) setUnresolvedPosition(false)
    }, [gate])

    // The text-nav pass could not find the preview anywhere: the audio rung
    // did NOT land. Unresolved semantics — banner, gate stays closed.
    const failProvisionalLanding = useCallback(() => {
        if (gate.failProvisionalLanding()) setUnresolvedPosition(true)
    }, [gate])

    // Opens the write gate if it may open, and says whether it is open.
    // Reads only refs so the mount-time `relocated` handler can call it.
    const maybeOpenGate = useCallback(() => {
        const verdict = gate.maybeOpen({
            spineIndex: latestPositionRef.current.spineIndex,
            chapterProgression: currentChapterProgressionRef.current,
        })
        if (verdict === 'opened') setUnresolvedPosition(false)
        return verdict !== 'closed'
    }, [gate])

    const noteUserNavigation = useCallback(() => {
        gate.noteUserNavigation()
    }, [gate])

    const extractVisibleText = useCallback(() => {
        const contents = renditionRef.current?.getContents?.()
        if (!contents || !contents.length) return ''
        const doc = contents[0]?.document
        if (!doc) return ''

        let rawText = ''

        // Primary: use the current CFI to get text at the exact reader position.
        // epub.js pagination splits pages by pixel height, not character count, so
        // (page-1)/total * rawText.length gives the wrong character position.
        // bookRef.current.epubcfi.toRange(cfi, doc) gives the actual DOM position.
        const location = renditionRef.current?.currentLocation?.()
        const startCfi = location?.start?.cfi
        if (startCfi && bookRef.current?.epubcfi) {
            try {
                const range = bookRef.current.epubcfi.toRange(startCfi, doc)
                if (range) {
                    // Extend from the CFI position to the end of body to capture text forward
                    const extRange = doc.createRange()
                    extRange.setStart(range.startContainer, range.startOffset)
                    extRange.setEnd(doc.body, doc.body.childNodes.length)
                    rawText = extRange.toString().substring(0, 350)
                }
            } catch (e) {
                // fall through to progression-based fallback
            }
        }

        // Fallback: progression-based character offset (less accurate but better than nothing)
        if (rawText.trim().length < 20) {
            const bodyText = doc.body?.innerText || ''
            const progression = currentChapterProgressionRef.current
            const charIndex = Math.floor(bodyText.length * progression)
            rawText = bodyText.substring(Math.max(0, charIndex - 20), Math.min(charIndex + 200, bodyText.length))
        }

        // Normalize with the shared matcher-parity pipeline (issue #305):
        // unicode whitespace variants become spaces instead of vanishing.
        let text = normalizeForSearch(rawText.substring(0, 300))
        // Strip book title from start (epub.js includes <title> text at top of chapters)
        if (bookTitle) {
            const titleNorm = normalizeForSearch(bookTitle)
            if (titleNorm && text.startsWith(titleNorm)) {
                text = text.slice(titleNorm.length).trim()
                if (text.startsWith(titleNorm)) text = text.slice(titleNorm.length).trim()
            }
        }
        return text.substring(0, 220)
    }, [bookTitle])


    // Writes the position. Returns true only when a write was actually
    // adjudicated by the server — false when the gate/text-nav suppressed it
    // or the request failed — so callers can be honest about what happened
    // (issue #159: the ✓ used to flash regardless).
    //
    // `explicit` marks a direct user command (the Save button): it always
    // writes and always claims the format.
    const doSave = useCallback(async (cfi, percent, spineIndex, { explicit = false } = {}) => {
        if (!cfi) return false
        // Don't save while text nav is hopping between chapters looking for text
        if (textNavInProgressRef.current) return false
        // Nothing may be written until we know where the reader actually is.
        // A restore that failed and left the book at page one would otherwise
        // persist chapter 0 over a real position set on another device.
        // `maybeOpenGate` applies the contract's navigation clause first: a
        // deliberate user page-turn (off the start of the book) reopens it.
        if (!maybeOpenGate()) {
            console.warn('[EbookReader] save suppressed: position not established yet')
            return false
        }
        // Locations generation re-reports the same spot with a refined
        // percent; an automatic save must not treat that drift as movement
        // (production wrote the same page twice, 8s apart, at percent
        // 0.0 → 0.1). An unchanged cfi is not a new position.
        if (!explicit && cfi === lastIssuedSaveRef.current) return true
        const chapter = spineIndex ?? currentSpineIndexRef.current
        const capturedAt = new Date().toISOString()
        console.log(`[EbookReader] doSave: chapter=${chapter}, pairId=${pairId}, percent=${percent?.toFixed(1)}`)
        try {
            // Sync-map match (paired books only) upgrades the anchor to a
            // sentence and its audio position. A miss is not a failure — the
            // chapter + preview anchor still describes the position.
            //
            // Only for a GENUINELY landed restore. When the gate reopened via
            // the navigation clause instead (failed/unconfirmed landing), the
            // matcher stays out of the write: matching the wrong text mints a
            // wrong audio_position_ms that overwrites a real audio anchor —
            // the exact production loss. Chapter + preview is a full
            // position; omitting audio_position_ms leaves the stored one
            // alone.
            const textPreview = extractVisibleText()
            let match = null
            if (gate.restoreLanded && pairId && textPreview && textPreview.length > 10) {
                match = await matchTextToAudio(pairId, textPreview, chapter).catch(e => {
                    console.warn('[EbookReader] matchTextToAudio failed:', e.message || e)
                    return null
                })
            }

            // ONE write carrying the whole position. Two writes (progress +
            // bookmark) were adjudicated separately, so one could be accepted
            // while the other was rejected and the two rows would then
            // disagree about where the reader was, permanently.
            const position = {
                epub_chapter: match ? match.epub_chapter : chapter,
                epub_sentence_index: match ? match.epub_sentence_index : undefined,
                // A sentence index is a sync-map coordinate; attest which map
                // it was resolved against so the server can tell a stale
                // index from a current one (issue #116).
                sync_map_version: match ? match.sync_map_version : undefined,
                epub_text_preview: textPreview || undefined,
                epub_progress_percent: Math.round(percent * 100) / 100,
                audio_position_ms: match ? match.audio_position_ms : undefined,
                hint: { kind: 'epubjs_cfi', value: cfi },
                // Stamped when the page turn happened, not after the sync-map
                // round-trip above — the write attests when the reader was
                // there (contract, "The write gate").
                captured_at: capturedAt,
            }
            const result = await writePosition(
                positionTarget({ pair_id: pairId }, 'ebook', ebookId),
                position,
                // Only a foreground, user-initiated write claims the format. A
                // settle relocation the user never asked for must not flip a
                // listen-only pair to `ebook` — omission means "keep the
                // stored value".
                { claimSource: (explicit || gate.userNavigated) ? 'ebook' : null },
            )
            // The write reached the server and was adjudicated; a later flush
            // for the same CFI would be a pure duplicate.
            lastIssuedSaveRef.current = cfi

            // A genuinely different device wrote something newer. Surface it;
            // never navigate on the user's behalf.
            const conflict = conflictFrom(result)
            if (conflict) {
                setStaleConflict({ cfi: conflict.cfi, deviceName: conflict.deviceName })
            }
            return true
        } catch (e) {
            console.warn('Failed to save reading progress:', e)
            return false
        }
    }, [ebookId, pairId, extractVisibleText, maybeOpenGate, gate])


    // Debounced progress save (auto-save on page turn)
    const saveProgress = useCallback((cfi, percent) => {
        if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
        saveTimerRef.current = setTimeout(() => {
            saveTimerRef.current = null
            doSave(cfi, percent)
        }, 2000)
    }, [doSave])

    // Builds the whole-position payload synchronously from the latest
    // relocated state. Shared by the unmount flush and the page-lifecycle
    // keepalive (issue #158), which must not await anything: chapter +
    // preview is a full position per the contract, so the sync-map matcher
    // upgrade doSave performs is deliberately skipped here.
    const buildFlushPayload = useCallback(() => {
        const { cfi, percent, spineIndex } = latestPositionRef.current
        if (!cfi) return null
        const textPreview = extractVisibleText()
        return {
            cfi,
            fields: {
                epub_chapter: spineIndex ?? currentSpineIndexRef.current,
                epub_text_preview: textPreview || undefined,
                epub_progress_percent: Math.round(percent * 100) / 100,
                hint: { kind: 'epubjs_cfi', value: cfi },
            },
            // Same rule as doSave: an automatic flush claims the format only
            // when the user actually navigated this session.
            claimSource: gate.userNavigated ? 'ebook' : null,
        }
    }, [extractVisibleText, gate])

    // Common core of the two flush paths. Clears the debounce timer, applies
    // the same gate as doSave, and skips when nothing moved since the last
    // issued write (idempotence). Returns the built payload to send, or null.
    const takeFlushablePosition = useCallback(() => {
        if (saveTimerRef.current) {
            clearTimeout(saveTimerRef.current)
            saveTimerRef.current = null
        }
        if (textNavInProgressRef.current) return null
        if (!maybeOpenGate()) return null
        const built = buildFlushPayload()
        if (!built) return null
        if (built.cfi === lastIssuedSaveRef.current) return null
        lastIssuedSaveRef.current = built.cfi
        return built
    }, [maybeOpenGate, buildFlushPayload])

    // Issue the pending position write immediately, fire-and-forget. Called
    // from the unmount cleanup (close, Escape, handoff, route change) — the
    // debounce timer used to be cancelled there with nothing flushed, so the
    // last page read was never written (issue #158). Must run while the epub
    // iframe still exists: extractVisibleText() needs its DOM.
    const flushPendingSave = useCallback(() => {
        const built = takeFlushablePosition()
        if (!built) return
        writePosition(positionTarget({ pair_id: pairId }, 'ebook', ebookId),
            built.fields, { claimSource: built.claimSource })
            .catch(e => console.warn('[EbookReader] unmount flush failed:', e?.message || e))
    }, [takeFlushablePosition, pairId, ebookId])

    // Ref mirror so the [ebookId] effect cleanup and the saveFlushRef prop
    // always call the current flush without re-running effects.
    const flushRef = useRef(() => {})
    flushRef.current = flushPendingSave

    // Let the parent force the flush BEFORE it starts the audio player, so
    // the reader's write is issued before the player's first
    // `source: 'audiobook'` write (issue #158 — the handoff used to drop the
    // ebook anchor and reopen the pair in the audiobook at a stale position).
    useEffect(() => {
        if (!saveFlushRef) return
        saveFlushRef.current = () => flushRef.current()
        return () => { saveFlushRef.current = null }
    }, [saveFlushRef])

    // Last-guaranteed-event flushes (issue #158): on iOS the PWA is the app —
    // beforeunload never fires and pagehide is unreliable; visibilitychange →
    // hidden is the last event that reliably runs. Regular fetch is aborted
    // during unload, so these go through the keepalive helper.
    useEffect(() => {
        const flushViaKeepalive = () => {
            const built = takeFlushablePosition()
            if (!built) return
            keepalivePosition(positionTarget({ pair_id: pairId }, 'ebook', ebookId),
                built.fields, { claimSource: built.claimSource })
        }
        const onVisibilityChange = () => {
            if (document.visibilityState === 'hidden') flushViaKeepalive()
        }
        window.addEventListener('pagehide', flushViaKeepalive)
        document.addEventListener('visibilitychange', onVisibilityChange)
        return () => {
            window.removeEventListener('pagehide', flushViaKeepalive)
            document.removeEventListener('visibilitychange', onVisibilityChange)
        }
    }, [takeFlushablePosition, pairId, ebookId])

    // Manual immediate save. The ✓ appears only when the write really went
    // through; otherwise a short "Not saved" — including while the gate is
    // closed after an unresolved restore (issue #159).
    const saveNow = useCallback(async () => {
        if (saveTimerRef.current) {
            clearTimeout(saveTimerRef.current)
            saveTimerRef.current = null
        }
        const wrote = await doSave(currentCfi, progressPercent, undefined, { explicit: true })
        setSaveState(wrote ? 'saved' : 'error')
        setTimeout(() => setSaveState(null), 1500)
    }, [doSave, currentCfi, progressPercent])

    // Everything that must happen between the TOC loading and the spinner
    // clearing: the canonical position fetch, the restore ladder, the gate
    // verdict, and only THEN the `relocated` handler — so its save path can
    // never fire before the restore has landed. A hoisted declaration: the
    // hook above is called before the callbacks this closes over exist.
    async function handleOpened({ book, rendition, nav, isDestroyed }) {
        // The canonical position fetch (with its offline fallback), the
        // ladder walk and the verdict all live in lib/restoreController.
        const restored = await restorePosition({
            book, rendition, pairId, ebookId, initialChapter, initialTextPreview, isDestroyed,
        })
        if (!restored) return
        positionRef.current = restored.position

        // A position we could not resolve is NOT the same as no position.
        // Saving stays blocked in that case, so a failed restore can never
        // overwrite a real position with page one — and an audio-derived
        // landing stays provisional until the text-nav pass below confirms
        // it (see `classifyLanding`).
        gate.applyLanding(restored.outcome)
        if (!isDestroyed()) setUnresolvedPosition(restored.outcome.unresolved)

        // Track position changes
        rendition.on('relocated', (location) => {
            if (isDestroyed()) return
            const cfi = location.start.cfi
            const percent = book.locations
                ? location.start.percentage * 100
                : (location.start.displayed?.page / location.start.displayed?.total) * 100 || 0
            setCurrentCfi(cfi)
            setProgressPercent(percent)

            // Track chapter-level progression for text extraction (mirrors Android)
            const page = location.start.displayed?.page || 1
            const total = location.start.displayed?.total || 1
            currentChapterProgressionRef.current = Math.max(0, page - 1) / Math.max(1, total)

            // Track spine index for bookmark syncing
            const spineIndex = book.spine.items.findIndex(item =>
                item.href && (location.start.href === item.href ||
                location.start.href.endsWith('/' + item.href) ||
                item.href.endsWith('/' + location.start.href))
            )
            if (spineIndex >= 0) currentSpineIndexRef.current = spineIndex

            // Keep the latest position readable from the unmount
            // flush and the lifecycle keepalive, which cannot rely on
            // state (the cleanup closes over the first render's).
            latestPositionRef.current = {
                cfi,
                percent,
                spineIndex: spineIndex >= 0 ? spineIndex : currentSpineIndexRef.current,
            }
            // A user-initiated relocation may reopen the write gate
            // (navigation clause) — do it here, not just at save
            // time, so the unresolved banner clears on the page turn.
            maybeOpenGate()

            saveProgress(cfi, percent, spineIndex >= 0 ? spineIndex : undefined)

            // On first render: if we have a text preview, navigate to it within the chapter
            // Tries current chapter first, then adjacent chapters (±1, ±2) to handle
            // sync map chapter numbering offset from epub spine indices
            // Prefer the canonical record's preview: the prop is a
            // page-load-time snapshot, the record is what the book
            // actually says now.
            const previewText = positionRef.current?.epub_text_preview || initialTextPreview
            if (!textNavDoneRef.current && previewText) {
                textNavDoneRef.current = true
                textNavInProgressRef.current = true

                // The preview is SERVER-extracted text; normalize it
                // with the matcher-parity pipeline or a needle
                // containing (say) an nbsp-adjacent word can never be
                // found in identically normalized section text.
                const targetNorm = normalizeForSearch(previewText)
                const shortTarget = targetNorm.substring(0, 30)

                if (shortTarget.length >= 5) {
                    // Search for target text in the currently rendered chapter content.
                    // Takes an explicit spineHref so we generate the CFI against the
                    // correct spine section (renditionRef.location can be stale after
                    // rapid chapter-hopping).
                    const trySearchChapter = (spineHref) => {
                        try {
                            const contents = renditionRef.current?.getContents?.()
                            const doc = contents?.[0]?.document
                            if (!doc) return null

                            const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT)
                            const boundaries = []
                            let accumulated = ''
                            let n
                            while ((n = walker.nextNode())) {
                                const norm = normalizeForSearch(n.textContent)
                                // Separator between text nodes — the server
                                // extracts previews with a separator at every
                                // tag boundary (get_text(separator="\n")), so
                                // inline markup must not glue words here either.
                                if (accumulated && !accumulated.endsWith(' ')) accumulated += ' '
                                boundaries.push({ node: n, start: accumulated.length, len: norm.length })
                                accumulated += norm
                            }

                            const idx = accumulated.indexOf(shortTarget)
                            if (idx < 0) return null

                            const boundary = boundaries.find(b => b.start <= idx && b.start + b.len > idx)
                            if (!boundary) return null

                            // Calculate the character offset within this text node
                            const nodeOffset = idx - boundary.start
                            // Map back to the original (un-normalized) text to get real offset
                            const origText = boundary.node.textContent
                            let realOffset = 0
                            let normCount = 0
                            for (let i = 0; i < origText.length && normCount < nodeOffset; i++) {
                                const ch = origText[i].toLowerCase()
                                const isKept = /[a-z0-9 ]/.test(ch) || WHITESPACE_VARIANT_CHAR_RE.test(ch)
                                if (isKept) normCount++
                                realOffset = i + 1
                            }

                            const range = doc.createRange()
                            range.setStart(boundary.node, Math.min(realOffset, origText.length))
                            range.setEnd(boundary.node, Math.min(realOffset, origText.length))

                            const section = bookRef.current?.spine.get(spineHref)
                            if (!section) {
                                console.warn(`[EbookReader] text nav: spine.get('${spineHref}') returned null`)
                                return null
                            }

                            const cfiStr = section.cfiFromRange(range)
                            console.log(`[EbookReader] text nav: found '${shortTarget}' in ${spineHref} → CFI ${cfiStr}`)
                            return cfiStr
                        } catch (e) {
                            console.warn('[EbookReader] text nav search error:', e.message)
                            return null
                        }
                    }

                    // Load one spine document's text WITHOUT rendering
                    // it (epub.js Section.load), extracted and
                    // normalized exactly like the server built the
                    // previews we search for (issue #305): a
                    // separator at every tag boundary, then the
                    // matcher-parity normalization. Null when the
                    // section can't be loaded.
                    const loadSectionText = async (idx) => {
                        const b = bookRef.current
                        const item = b?.spine?.items?.[idx]
                        if (!item || typeof item.load !== 'function') return null
                        try {
                            const contents = await item.load(
                                typeof b.load === 'function' ? b.load.bind(b) : undefined
                            )
                            const raw = extractSearchableText(contents)
                                || String(contents?.body?.textContent ?? '')
                            item.unload?.()
                            return normalizeForSearch(raw)
                        } catch (e) {
                            return null
                        }
                    }

                    // Search EVERY spine document, outward from the
                    // seed — Android parity (ReaderActivity
                    // .findSpineIndexForText walks the whole reading
                    // order the same way). The old ±2 window silently
                    // missed a real preview whenever the sync map's
                    // chapter axis was offset from the spine by the
                    // front matter, which is exactly how a live
                    // restore ended up on the Copyright page.
                    const findSpineIndexForText = async (target, seedIdx) => {
                        const n = (bookRef.current?.spine?.items || []).length
                        if (!n) return -1
                        const seed = seedIdx >= 0 && seedIdx < n ? seedIdx : Math.floor(n / 2)
                        for (let offset = 0; offset < n; offset++) {
                            const candidates = offset === 0
                                ? [seed] : [seed + offset, seed - offset]
                            for (const idx of candidates) {
                                if (idx < 0 || idx >= n) continue
                                const text = await loadSectionText(idx)
                                if (text && text.includes(target)) {
                                    console.log(`[EbookReader] text nav: found '${target}' in spine ${idx} (seed=${seed})`)
                                    return idx
                                }
                            }
                        }
                        return -1
                    }

                    setTimeout(async () => {
                        const spineItems = bookRef.current?.spine?.items || []
                        const baseChapter = currentSpineIndexRef.current

                        // Concludes the pass. Confirmation matters for
                        // audio-derived restores: found = the landing
                        // is real; not found = it was a guess and the
                        // session is unresolved (banner, gate closed).
                        const finish = (found) => {
                            if (found) confirmProvisionalLanding()
                            else failProvisionalLanding()
                            textNavInProgressRef.current = false
                        }

                        // Fast path: the text is usually in the
                        // chapter already rendered.
                        let cfiStr = trySearchChapter(spineItems[baseChapter]?.href)
                        let foundIdx = cfiStr ? baseChapter : -1

                        if (foundIdx < 0) {
                            foundIdx = await findSpineIndexForText(shortTarget, baseChapter)
                        }
                        if (foundIdx == null || foundIdx < 0) {
                            console.warn(`[EbookReader] text nav: '${shortTarget}' not found in any spine document, giving up`)
                            finish(false)
                            return
                        }

                        // Keep textNavInProgressRef true through the display() calls.
                        // Setting it false BEFORE display() (the previous bug) allowed
                        // doSave to run from the relocated event that display() fires,
                        // corrupting the just-restored bookmark.
                        // Also wait 3 s after a CFI navigation: book.locations.generate()
                        // fires reportLocation() asynchronously, which emits another
                        // relocated and overwrites our position if not suppressed.
                        try {
                            if (foundIdx !== baseChapter) {
                                await renditionRef.current?.display(spineItems[foundIdx].href)
                                // Wait for epub.js to fully render the new chapter content
                                await new Promise(r => setTimeout(r, 300))
                            }
                            if (!cfiStr) cfiStr = trySearchChapter(spineItems[foundIdx].href)
                            if (cfiStr) {
                                await renditionRef.current?.display(cfiStr)
                                console.log(`[EbookReader] text nav: navigated to CFI successfully`)
                                await new Promise(r => setTimeout(r, 3000))
                            }
                        } catch (e) {
                            console.warn('[EbookReader] text nav: navigation failed:', e.message)
                        } finally {
                            // The text WAS located (rendered or in the
                            // section source) — the landing is real
                            // even when the precise CFI could not be
                            // computed; the chapter is right.
                            finish(true)
                        }
                    }, 100)
                } else {
                    // Too short to search: an audio-derived guess can
                    // never be confirmed by it.
                    failProvisionalLanding()
                    textNavInProgressRef.current = false
                }
            }

            // Find current chapter
            const currentSection = book.spine.get(location.start.href)
            if (currentSection && nav.toc) {
                const chapter = nav.toc.find(t =>
                    t.href && location.start.href.includes(t.href.split('#')[0])
                )
                if (chapter) setCurrentChapter(chapter.label?.trim() || '')
            }
        })
    }

    // Runs on unmount / ebookId change, before the hook destroys the book.
    // Flush the pending save BEFORE destroying the book: the payload's text
    // preview is extracted from the epub iframe DOM, which destroy() tears
    // down. Fire-and-forget — the write is issued synchronously, its
    // response nobody needs (issue #158).
    function handleTeardown() {
        try { flushRef.current() } catch (e) {}
        if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
    }

    // Keyboard navigation
    useEffect(() => {
        function handleKey(e) {
            if (e.key === 'ArrowLeft' || e.key === 'PageUp') {
                e.preventDefault()
                noteUserNavigation()
                renditionRef.current?.prev()
            } else if (e.key === 'ArrowRight' || e.key === 'PageDown' || e.key === ' ') {
                e.preventDefault()
                noteUserNavigation()
                renditionRef.current?.next()
            } else if (e.key === 'Escape') {
                onClose()
            }
        }
        window.addEventListener('keydown', handleKey)
        return () => window.removeEventListener('keydown', handleKey)
    }, [onClose, noteUserNavigation])

    // Font size changes — inject via <style> tag to avoid blob URL MIME rejection
    useEffect(() => {
        fontSizeRef.current = fontSize
        localStorage.setItem(FONT_SIZE_KEY, String(fontSize))
        if (renditionRef.current) {
            renditionRef.current.getContents().forEach(c => {
                if (c.document) {
                    let style = c.document.getElementById('tandem-font-size')
                    if (!style) {
                        style = c.document.createElement('style')
                        style.id = 'tandem-font-size'
                        c.document.head.appendChild(style)
                    }
                    style.textContent = `html { font-size: ${fontSize}% !important; }`
                }
            })
        }
    }, [fontSize])

    // Palette changes (reader mode or app theme) — restyle already-rendered
    // chapter documents; newly rendered ones get it from the content hook.
    useEffect(() => {
        if (renditionRef.current) {
            renditionRef.current.getContents().forEach(c => {
                if (c.document) {
                    let style = c.document.getElementById('tandem-reader-theme')
                    if (!style) {
                        style = c.document.createElement('style')
                        style.id = 'tandem-reader-theme'
                        c.document.head.appendChild(style)
                    }
                    style.textContent = paletteCss(palette)
                }
            })
        }
    }, [palette.background, palette.text, palette.link]) // eslint-disable-line react-hooks/exhaustive-deps

    const chooseReaderTheme = useCallback((mode) => {
        setReaderTheme(mode)
        localStorage.setItem(READER_THEME_KEY, mode)
        setShowThemeMenu(false)
    }, [])

    const handleTocClick = (href) => {
        noteUserNavigation()
        renditionRef.current?.display(href)
        setShowToc(false)
    }

    return (
        <div className="ebook-reader-overlay">
            {/* Toolbar */}
            <div className="ebook-toolbar">
                <div className="ebook-toolbar-left">
                    <button className="btn-icon" onClick={onClose} title="Close reader">
                        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2">
                            <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
                        </svg>
                    </button>
                    <button className="btn-icon" onClick={() => setShowToc(true)} title="Table of contents">
                        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2">
                            <line x1="3" y1="6" x2="21" y2="6" /><line x1="3" y1="12" x2="21" y2="12" /><line x1="3" y1="18" x2="21" y2="18" />
                        </svg>
                    </button>
                    <span className="ebook-book-title">{bookTitle || 'Reading'}</span>
                </div>
                <div className="ebook-toolbar-center">
                    {currentChapter && (
                        <span className="ebook-progress-text">{currentChapter}</span>
                    )}
                </div>
                <div className="ebook-toolbar-right">
                    {onSwitchToAudio && pairId && (
                        <button
                            className="btn-icon switch-format-btn"
                            onClick={onSwitchToAudio}
                            title="Switch to Audiobook"
                        >
                            <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2">
                                <path d="M3 18v-6a9 9 0 0 1 18 0v6" />
                                <path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3z" />
                                <path d="M3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z" />
                            </svg>
                            <span style={{ fontSize: 12, marginLeft: 4 }}>Listen</span>
                        </button>
                    )}
                    <div className="reader-theme-picker">
                        <button
                            className="btn-icon"
                            onClick={() => setShowThemeMenu(s => !s)}
                            title="Reader theme"
                        >
                            <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2">
                                <circle cx="12" cy="12" r="9" />
                                <path d="M12 3a9 9 0 0 0 0 18z" fill="currentColor" stroke="none" />
                            </svg>
                        </button>
                        {showThemeMenu && (
                            <div className="reader-theme-menu">
                                {READER_MODE_LABELS.map(([mode, label]) => (
                                    <button
                                        key={mode}
                                        className={readerTheme === mode ? 'active' : ''}
                                        onClick={() => chooseReaderTheme(mode)}
                                    >
                                        {label}
                                    </button>
                                ))}
                            </div>
                        )}
                    </div>
                    <div className="font-size-controls">
                        <button onClick={() => setFontSize(s => Math.max(MIN_FONT, s - 10))} title="Decrease font">A-</button>
                        <button onClick={() => setFontSize(s => Math.min(MAX_FONT, s + 10))} title="Increase font">A+</button>
                    </div>
                    <span className="ebook-progress-text">{progressPercent.toFixed(1)}%</span>
                    {saveState === 'error' && (
                        <span className="ebook-progress-text" style={{ color: 'var(--error)' }}>
                            Not saved
                        </span>
                    )}
                    <button
                        className={`btn-icon${saveState === 'saved' ? ' saved' : ''}`}
                        onClick={saveNow}
                        title="Save position"
                    >
                        {saveState === 'saved' ? (
                            <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2">
                                <polyline points="20 6 9 17 4 12" />
                            </svg>
                        ) : (
                            <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2">
                                <path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z" />
                            </svg>
                        )}
                    </button>
                </div>
            </div>

            {/* Stale-conflict banner (issue #54): a different device's write was
                newer than ours and won. Never auto-navigate -- only jump on an
                explicit click. */}
            {staleConflict && (
                <div className="alert alert-warning" style={{ margin: '8px 16px', display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ flex: 1 }}>
                        Newer position available from {staleConflict.deviceName}
                        {staleConflict.cfi ? '' : ' on this book'}
                    </span>
                    {staleConflict.cfi ? (
                        <button
                            className="btn btn-sm btn-secondary"
                            onClick={() => {
                                renditionRef.current?.display(staleConflict.cfi)
                                setStaleConflict(null)
                            }}
                        >
                            Jump
                        </button>
                    ) : (
                        <button
                            className="btn btn-sm btn-secondary"
                            onClick={() => setStaleConflict(null)}
                        >
                            Dismiss
                        </button>
                    )}
                </div>
            )}

            {/* Unresolved-restore banner (issue #159): the record holds a
                position this reader could not resolve. Saving is blocked (so
                page one can't overwrite a real anchor) until the user turns a
                page — say so instead of failing silently. Dismiss hides the
                banner; the gate itself only reopens via the navigation
                clause. */}
            {unresolvedPosition && (
                <div className="alert alert-warning" style={{ margin: '8px 16px', display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ flex: 1 }}>
                        Couldn't find your saved place in this book. Turn a page to save from here.
                    </span>
                    <button
                        className="btn btn-sm btn-secondary"
                        onClick={() => setUnresolvedPosition(false)}
                    >
                        Dismiss
                    </button>
                </div>
            )}

            {/* Reader area */}
            <div className="ebook-reader-container">
                {loading && (
                    <div className="ebook-loading">
                        <div className="spinner"></div>
                        <span>Loading ebook...</span>
                    </div>
                )}
                {error && (
                    <div className="ebook-loading">
                        <span style={{ color: 'var(--error)' }}>{error}</span>
                        <button className="btn btn-secondary" onClick={onClose}>Close</button>
                    </div>
                )}

                <button className="ebook-nav-btn prev" onClick={() => { noteUserNavigation(); renditionRef.current?.prev() }} title="Previous page">
                    <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" strokeWidth="2">
                        <polyline points="15 18 9 12 15 6" />
                    </svg>
                </button>

                <div ref={viewerRef} className="ebook-viewer" />

                <button className="ebook-nav-btn next" onClick={() => { noteUserNavigation(); renditionRef.current?.next() }} title="Next page">
                    <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" strokeWidth="2">
                        <polyline points="9 18 15 12 9 6" />
                    </svg>
                </button>

                {/* TOC panel */}
                {showToc && (
                    <div className="ebook-toc-overlay" onClick={() => setShowToc(false)}>
                        <div className="ebook-toc-panel" onClick={e => e.stopPropagation()}>
                            <div className="ebook-toc-header">
                                <h3>Table of Contents</h3>
                                <button className="btn-icon" onClick={() => setShowToc(false)}>
                                    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2">
                                        <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
                                    </svg>
                                </button>
                            </div>
                            <div className="ebook-toc-list">
                                {toc.map((item, i) => (
                                    <button
                                        key={i}
                                        className={`ebook-toc-item${currentChapter === item.label?.trim() ? ' active' : ''}`}
                                        onClick={() => handleTocClick(item.href)}
                                    >
                                        {item.label?.trim()}
                                    </button>
                                ))}
                            </div>
                        </div>
                    </div>
                )}
            </div>

            {/* Bottom progress bar */}
            <div className="ebook-progress-bar">
                <div className="ebook-progress-bar-fill" style={{ width: `${progressPercent}%` }} />
            </div>
        </div>
    )
}

export default EbookReader
