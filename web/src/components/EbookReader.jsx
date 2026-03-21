import React, { useEffect, useRef, useState, useCallback } from 'react'
import ePub from 'epubjs'
import { fetchEbookBlob, updateProgress, updateBookmark } from '../api'
import './EbookReader.css'

function EbookReader({ ebookId, pairId, initialCfi, onClose, bookTitle }) {
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
    const [savedIndicator, setSavedIndicator] = useState(false)

    const doSave = useCallback(async (cfi, percent) => {
        if (!cfi) return
        try {
            await updateProgress('ebook', ebookId, {
                epub_cfi: cfi,
                epub_progress_percent: Math.round(percent * 100) / 100,
                device_id: 'web',
            })
            if (pairId) {
                await updateBookmark(pairId, {
                    source: 'ebook',
                    epub_locator: JSON.stringify({ cfi }),
                }).catch(() => {})
            }
        } catch (e) {
            console.warn('Failed to save reading progress:', e)
        }
    }, [ebookId, pairId])

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

                rendition.themes.fontSize(`${fontSize}%`)

                // Load TOC
                const nav = await book.loaded.navigation
                if (!destroyed) {
                    setToc(nav.toc || [])
                }

                // Display at saved position or start
                if (initialCfi) {
                    await rendition.display(initialCfi)
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
                    saveProgress(cfi, percent)

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

    // Font size changes
    useEffect(() => {
        if (renditionRef.current) {
            renditionRef.current.themes.fontSize(`${fontSize}%`)
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
