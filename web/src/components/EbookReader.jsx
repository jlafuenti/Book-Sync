import React, { useEffect, useRef, useState, useCallback } from 'react'
import ePub from 'epubjs'
import { fetchEbookBlob, updateProgress, updateBookmark, matchTextToAudio, getDeviceId, getDeviceName } from '../api'
import './EbookReader.css'

function EbookReader({ ebookId, pairId, initialCfi, initialChapter, initialTextPreview, onClose, bookTitle, onSwitchToAudio }) {
    const viewerRef = useRef(null)
    const bookRef = useRef(null)
    const renditionRef = useRef(null)
    const saveTimerRef = useRef(null)

    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)
    const [toc, setToc] = useState([])
    const [showToc, setShowToc] = useState(false)
    const [currentCfi, setCurrentCfi] = useState(initialCfi)
    const [progressPercent, setProgressPercent] = useState(0)
    const [currentChapter, setCurrentChapter] = useState('')
    const [fontSize, setFontSize] = useState(100)
    const fontSizeRef = useRef(100)
    const [savedIndicator, setSavedIndicator] = useState(false)
    const currentSpineIndexRef = useRef(initialChapter ?? 0)
    // Tracks progression (0-1) within the current chapter, updated on each page turn
    const currentChapterProgressionRef = useRef(0)
    // Whether we've done the initial text-based navigation (only do it once)
    const textNavDoneRef = useRef(false)
    // Suppress auto-saves while text nav is hopping between chapters
    const textNavInProgressRef = useRef(false)

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

        // Normalize: lowercase, whitespace→spaces, strip non-alphanumeric, collapse spaces
        let text = rawText.substring(0, 300)
            .toLowerCase()
            .replace(/[\n\r\t]/g, ' ')
            .replace(/[^a-z0-9 ]/g, '')
            .replace(/ +/g, ' ')
            .trim()
        // Strip book title from start (epub.js includes <title> text at top of chapters)
        if (bookTitle) {
            const titleNorm = bookTitle.toLowerCase().replace(/[^a-z0-9 ]/g, '').replace(/ +/g, ' ').trim()
            if (titleNorm && text.startsWith(titleNorm)) {
                text = text.slice(titleNorm.length).trim()
                if (text.startsWith(titleNorm)) text = text.slice(titleNorm.length).trim()
            }
        }
        return text.substring(0, 220)
    }, [bookTitle])

    const doSave = useCallback(async (cfi, percent, spineIndex) => {
        if (!cfi) return
        // Don't save while text nav is hopping between chapters looking for text
        if (textNavInProgressRef.current) return
        const chapter = spineIndex ?? currentSpineIndexRef.current
        // Captured once so every write from this save (progress + whichever
        // bookmark branch below fires) reports the same read-moment timestamp.
        const capturedAt = new Date().toISOString()
        console.log(`[EbookReader] doSave: chapter=${chapter}, pairId=${pairId}, percent=${percent?.toFixed(1)}, chapterProgression=${currentChapterProgressionRef.current?.toFixed(3)}`)
        try {
            await updateProgress('ebook', ebookId, {
                epub_cfi: cfi,
                epub_chapter: chapter,
                epub_progress_percent: Math.round(percent * 100) / 100,
                book_pair_id: pairId || undefined,
                device_id: getDeviceId(),
                device_name: getDeviceName(),
                captured_at: capturedAt,
            })
            if (pairId) {
                // Extract visible text and match against sync map for accurate audio position
                const textPreview = extractVisibleText()
                console.log(`[EbookReader] textPreview (${textPreview?.length} chars): '${textPreview?.substring(0, 80)}...'`)
                if (textPreview && textPreview.length > 10) {
                    const match = await matchTextToAudio(pairId, textPreview, chapter).catch(e => {
                        console.warn(`[EbookReader] matchTextToAudio failed:`, e.message || e)
                        return null
                    })
                    if (match) {
                        console.log(`[EbookReader] MATCH: ch${match.epub_chapter} s${match.epub_sentence_index} audio=${match.audio_position_ms}ms, preview='${match.preview?.substring(0, 60)}'`)
                        await updateBookmark(pairId, {
                            source: 'ebook',
                            epub_chapter: match.epub_chapter,
                            epub_sentence_index: match.epub_sentence_index,
                            audio_position_ms: match.audio_position_ms,
                            device_id: getDeviceId(),
                            device_name: getDeviceName(),
                            captured_at: capturedAt,
                        }).catch(() => {})
                    } else {
                        console.warn(`[EbookReader] No match found — saving epub position only`)
                        // No match — save epub position only, don't corrupt audio position
                        await updateBookmark(pairId, {
                            source: 'ebook',
                            epub_chapter: chapter,
                            device_id: getDeviceId(),
                            device_name: getDeviceName(),
                            captured_at: capturedAt,
                        }).catch(() => {})
                    }
                } else {
                    console.warn(`[EbookReader] Text too short for matching (${textPreview?.length} chars), saving chapter only`)
                    await updateBookmark(pairId, {
                        source: 'ebook',
                        epub_chapter: chapter,
                        device_id: getDeviceId(),
                        device_name: getDeviceName(),
                        captured_at: capturedAt,
                    }).catch(() => {})
                }
            } else {
                console.log(`[EbookReader] No pairId, skipping bookmark sync`)
            }
        } catch (e) {
            console.warn('Failed to save reading progress:', e)
        }
    }, [ebookId, pairId, extractVisibleText])

    // Debounced progress save (auto-save on page turn)
    const saveProgress = useCallback((cfi, percent) => {
        if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
        saveTimerRef.current = setTimeout(() => doSave(cfi, percent), 2000)
    }, [doSave])

    // Manual immediate save
    const saveNow = useCallback(async () => {
        if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
        await doSave(currentCfi, progressPercent)
        setSavedIndicator(true)
        setTimeout(() => setSavedIndicator(false), 1500)
    }, [doSave, currentCfi, progressPercent])

    // Initialize epub
    useEffect(() => {
        let destroyed = false

        async function loadBook() {
            try {
                const arrayBuffer = await fetchEbookBlob(ebookId)
                if (destroyed) return

                const book = ePub(arrayBuffer)
                bookRef.current = book

                const rendition = book.renderTo(viewerRef.current, {
                    width: '100%',
                    height: '100%',
                    flow: 'paginated',
                    spread: 'none',
                })
                renditionRef.current = rendition

                // Apply dark theme
                rendition.themes.default({
                    'body': {
                        'background': '#0f0f1a !important',
                        'color': '#e8e8f0 !important',
                        'font-family': 'Georgia, "Times New Roman", serif !important',
                        'padding': '0 48px !important',
                        'max-width': '100% !important',
                        'box-sizing': 'border-box !important',
                    },
                    'a': { 'color': '#a78bfa !important' },
                    'h1, h2, h3, h4, h5, h6': { 'color': '#e8e8f0 !important' },
                    'p, span, div, li, td, th': { 'color': '#e8e8f0 !important' },
                    'img': { 'max-width': '100% !important' },
                    '*': { 'max-width': '100% !important', 'box-sizing': 'border-box !important' },
                })

                // Inject font size via <style> tag (avoids blob URL MIME rejection)
                rendition.hooks.content.register(contents => {
                    if (contents.document) {
                        const style = contents.document.createElement('style')
                        style.id = 'tandem-font-size'
                        style.textContent = `html { font-size: ${fontSizeRef.current}% !important; }`
                        contents.document.head.appendChild(style)
                    }
                })

                // Load TOC
                const nav = await book.loaded.navigation
                if (!destroyed) {
                    setToc(nav.toc || [])
                }

                // Display at saved position or chapter, or start
                if (initialCfi) {
                    await rendition.display(initialCfi)
                } else if (initialChapter != null && initialChapter >= 0 && book.spine.items[initialChapter]) {
                    await rendition.display(book.spine.items[initialChapter].href)
                } else {
                    await rendition.display()
                }

                if (!destroyed) setLoading(false)

                // Track position changes
                rendition.on('relocated', (location) => {
                    if (destroyed) return
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

                    saveProgress(cfi, percent, spineIndex >= 0 ? spineIndex : undefined)

                    // On first render: if we have a text preview, navigate to it within the chapter
                    // Tries current chapter first, then adjacent chapters (±1, ±2) to handle
                    // sync map chapter numbering offset from epub spine indices
                    if (!textNavDoneRef.current && initialTextPreview) {
                        textNavDoneRef.current = true
                        textNavInProgressRef.current = true

                        const targetNorm = initialTextPreview.toLowerCase()
                            .replace(/[\n\r\t]/g, ' ').replace(/[^a-z0-9 ]/g, '').replace(/ +/g, ' ').trim()
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
                                        const norm = n.textContent.toLowerCase()
                                            .replace(/[\n\r\t]/g, ' ').replace(/[^a-z0-9 ]/g, '').replace(/ +/g, ' ')
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
                                        const isKept = /[a-z0-9 ]/.test(ch) || /[\n\r\t]/.test(ch)
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

                            setTimeout(async () => {
                                const spineItems = bookRef.current?.spine?.items || []
                                const baseChapter = currentSpineIndexRef.current
                                const offsets = [0, 1, -1, 2, -2]

                                for (const offset of offsets) {
                                    const tryChapter = baseChapter + offset
                                    if (tryChapter < 0 || tryChapter >= spineItems.length) continue
                                    const href = spineItems[tryChapter].href

                                    // Navigate to this chapter if it's not the initial one
                                    if (offset !== 0) {
                                        console.log(`[EbookReader] text nav: '${shortTarget}' not in chapter ${tryChapter - (offset > 0 ? 1 : -1)}, trying chapter ${tryChapter}`)
                                        try {
                                            await renditionRef.current?.display(href)
                                            // Wait for epub.js to fully render the new chapter content
                                            await new Promise(r => setTimeout(r, 300))
                                        } catch (e) {
                                            console.warn(`[EbookReader] text nav: failed to display chapter ${tryChapter}:`, e.message)
                                            continue
                                        }
                                    }

                                    const cfiStr = trySearchChapter(href)
                                    if (cfiStr) {
                                        // Keep textNavInProgressRef true through the display() call.
                                        // Setting it false BEFORE display() (the previous bug) allowed
                                        // doSave to run from the relocated event that display() fires,
                                        // corrupting the just-restored bookmark.
                                        // Also wait 3 s after navigation: book.locations.generate()
                                        // fires reportLocation() asynchronously, which emits another
                                        // relocated and overwrites our position if not suppressed.
                                        try {
                                            await renditionRef.current?.display(cfiStr)
                                            console.log(`[EbookReader] text nav: navigated to CFI successfully`)
                                            await new Promise(r => setTimeout(r, 3000))
                                        } catch (e) {
                                            console.warn(`[EbookReader] text nav: display(cfi) failed:`, e.message)
                                        } finally {
                                            textNavInProgressRef.current = false
                                        }
                                        return
                                    }
                                }

                                // Exhausted all attempts
                                console.warn(`[EbookReader] text nav: '${shortTarget}' not found in chapters ${baseChapter}±2, giving up`)
                                textNavInProgressRef.current = false
                            }, 100)
                        } else {
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

                // Generate locations for accurate percentages
                book.ready.then(() => {
                    return book.locations.generate(1024)
                }).then(() => {
                    if (!destroyed && renditionRef.current) {
                        renditionRef.current.reportLocation()
                    }
                })

            } catch (err) {
                if (!destroyed) {
                    setError(err.message || 'Failed to load ebook')
                    setLoading(false)
                }
            }
        }

        loadBook()

        return () => {
            destroyed = true
            if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
            if (bookRef.current) {
                try { bookRef.current.destroy() } catch (e) {}
            }
        }
    }, [ebookId]) // eslint-disable-line react-hooks/exhaustive-deps

    // Keyboard navigation
    useEffect(() => {
        function handleKey(e) {
            if (e.key === 'ArrowLeft' || e.key === 'PageUp') {
                e.preventDefault()
                renditionRef.current?.prev()
            } else if (e.key === 'ArrowRight' || e.key === 'PageDown' || e.key === ' ') {
                e.preventDefault()
                renditionRef.current?.next()
            } else if (e.key === 'Escape') {
                onClose()
            }
        }
        window.addEventListener('keydown', handleKey)
        return () => window.removeEventListener('keydown', handleKey)
    }, [onClose])

    // Font size changes — inject via <style> tag to avoid blob URL MIME rejection
    useEffect(() => {
        fontSizeRef.current = fontSize
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

    const handleTocClick = (href) => {
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
                    <div className="font-size-controls">
                        <button onClick={() => setFontSize(s => Math.max(60, s - 10))} title="Decrease font">A-</button>
                        <button onClick={() => setFontSize(s => Math.min(200, s + 10))} title="Increase font">A+</button>
                    </div>
                    <span className="ebook-progress-text">{progressPercent.toFixed(1)}%</span>
                    <button
                        className={`btn-icon${savedIndicator ? ' saved' : ''}`}
                        onClick={saveNow}
                        title="Save position"
                    >
                        {savedIndicator ? (
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

                <button className="ebook-nav-btn prev" onClick={() => renditionRef.current?.prev()} title="Previous page">
                    <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" strokeWidth="2">
                        <polyline points="15 18 9 12 15 6" />
                    </svg>
                </button>

                <div ref={viewerRef} className="ebook-viewer" />

                <button className="ebook-nav-btn next" onClick={() => renditionRef.current?.next()} title="Next page">
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
