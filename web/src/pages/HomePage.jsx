import React, { useState, useEffect, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import {
    getAllProgress, getEbooks, getAudiobooks, getPairs, getTranscriptionQueue,
    updatePosition, resetPairProgress, resetPosition, getProgress as apiGetProgress, getPosition,
    getDeviceId, getDeviceName,
} from '../api'
import { useAudioPlayer } from '../contexts/AudioPlayerContext'
import useIsMobile from '../hooks/useIsMobile'
import useCoverSrc from '../hooks/useCoverSrc'
import EbookReader from '../components/EbookReader'
import { AudioPlayerView } from '../components/AudioPlayer'
import CoverImg from '../components/CoverImg'
import './HomePage.css'

// ---- Helpers ----

function formatTime(ms) {
    if (!ms) return '0:00'
    const totalSec = Math.floor(ms / 1000)
    const h = Math.floor(totalSec / 3600)
    const m = Math.floor((totalSec % 3600) / 60)
    const s = totalSec % 60
    if (h > 0) return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
    return `${m}:${s.toString().padStart(2, '0')}`
}

function calcProgress(item, audiobooks) {
    if (item.itemType === 'pair') {
        if (item.lastFormat === 'audiobook' && item.audioProgress) {
            const durSec = audiobooks[item.audiobookId]?.duration_seconds
            const val = durSec ? ((item.audioProgress.positionMs / 1000) / durSec) * 100 : 0
            return { value: val, label: formatTime(item.audioProgress.positionMs) }
        } else if (item.ebookProgress) {
            return { value: item.ebookProgress.percent, label: `${Math.round(item.ebookProgress.percent)}%` }
        } else if (item.audioProgress) {
            const durSec = audiobooks[item.audiobookId]?.duration_seconds
            const val = durSec ? ((item.audioProgress.positionMs / 1000) / durSec) * 100 : 0
            return { value: val, label: formatTime(item.audioProgress.positionMs) }
        }
        return { value: 0, label: '' }
    } else if (item.itemType === 'ebook') {
        const val = item.epub_progress_percent || 0
        return { value: val, label: `${Math.round(val)}%` }
    } else {
        const durSec = item.book?.duration_seconds
        const val = durSec ? ((item.audio_position_ms / 1000) / durSec) * 100 : 0
        return { value: val, label: formatTime(item.audio_position_ms) }
    }
}

// ---- Carousel ----

function Carousel({ children, className = '' }) {
    const scrollRef = useRef(null)
    const [canLeft, setCanLeft] = useState(false)
    const [canRight, setCanRight] = useState(false)

    const updateArrows = useCallback(() => {
        const el = scrollRef.current
        if (!el) return
        setCanLeft(el.scrollLeft > 4)
        setCanRight(el.scrollLeft + el.clientWidth < el.scrollWidth - 4)
    }, [])

    useEffect(() => {
        const el = scrollRef.current
        if (!el) return
        updateArrows()
        el.addEventListener('scroll', updateArrows, { passive: true })
        const ro = new ResizeObserver(updateArrows)
        ro.observe(el)
        return () => {
            el.removeEventListener('scroll', updateArrows)
            ro.disconnect()
        }
    }, [updateArrows, children])

    const scroll = (dir) => {
        const el = scrollRef.current
        if (!el) return
        el.scrollBy({ left: dir * (el.clientWidth * 0.75), behavior: 'smooth' })
    }

    return (
        <div className={`home-carousel-wrap ${className}`}>
            {canLeft && (
                <button className="home-carousel-arrow home-carousel-arrow-left" onClick={() => scroll(-1)}>
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" width="18" height="18">
                        <polyline points="15 18 9 12 15 6" />
                    </svg>
                </button>
            )}
            <div className="home-carousel" ref={scrollRef}>
                {children}
            </div>
            {canRight && (
                <button className="home-carousel-arrow home-carousel-arrow-right" onClick={() => scroll(1)}>
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" width="18" height="18">
                        <polyline points="9 18 15 12 9 6" />
                    </svg>
                </button>
            )}
        </div>
    )
}

// ---- Book Card ----

export function BookCard({ book, size = 'continue', progress, onPrimary, onRead, onListen, onMarkComplete, onResetProgress, onViewDetails, isPair, isEbook }) {
    const [menuOpen, setMenuOpen] = useState(false)
    const menuRef = useRef(null)

    useEffect(() => {
        if (!menuOpen) return
        const handler = (e) => {
            if (menuRef.current && !menuRef.current.contains(e.target)) setMenuOpen(false)
        }
        window.addEventListener('mousedown', handler)
        return () => window.removeEventListener('mousedown', handler)
    }, [menuOpen])

    const coverUrl = useCoverSrc(book?.cover_path)

    return (
        <div className={`home-book-card ${size}-size`} onClick={onPrimary}>
            <div className="home-book-card-cover">
                {coverUrl ? (
                    <img src={coverUrl} alt={book?.title} loading="lazy" />
                ) : (
                    <div className="home-book-card-placeholder">
                        <span>{isPair ? '📖' : isEbook ? '📚' : '🎧'}</span>
                    </div>
                )}
                {progress != null && (
                    <div className="home-book-card-progress-bar">
                        <div className="home-book-card-progress-fill" style={{ width: `${Math.min(progress, 100)}%` }} />
                    </div>
                )}
                {size === 'continue' && (onRead || onListen) && (
                    <div className="home-book-card-overlay" onClick={e => e.stopPropagation()}>
                        {onRead && (
                            <button className="overlay-btn" title="Read" onClick={onRead}>
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="20" height="20">
                                    <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" /><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
                                </svg>
                            </button>
                        )}
                        {onListen && (
                            <button className="overlay-btn" title="Listen" onClick={onListen}>
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="20" height="20">
                                    <path d="M3 18v-6a9 9 0 0 1 18 0v6" /><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3z" /><path d="M3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z" />
                                </svg>
                            </button>
                        )}
                    </div>
                )}
                {size === 'continue' && (
                    <div className="home-book-card-menu" ref={menuRef} onClick={e => e.stopPropagation()}>
                        <button
                            className="home-book-card-menu-btn"
                            onClick={() => setMenuOpen(o => !o)}
                            title="More options"
                        >
                            <svg viewBox="0 0 24 24" fill="currentColor" width="16" height="16">
                                <circle cx="12" cy="5" r="1.5" /><circle cx="12" cy="12" r="1.5" /><circle cx="12" cy="19" r="1.5" />
                            </svg>
                        </button>
                        {menuOpen && (
                            <div className="home-book-card-dropdown">
                                {onMarkComplete && <button onClick={() => { setMenuOpen(false); onMarkComplete() }}>Mark Complete</button>}
                                {onResetProgress && <button onClick={() => { setMenuOpen(false); onResetProgress() }}>Reset Progress</button>}
                                {onViewDetails && <button onClick={() => { setMenuOpen(false); onViewDetails() }}>View Details</button>}
                            </div>
                        )}
                    </div>
                )}
            </div>
            <div className="home-book-card-title" title={book?.title}>{book?.title}</div>
            {book?.author && <div className="home-book-card-author">{book.author}</div>}
        </div>
    )
}

// ---- Main Component ----

function HomePage() {
    const navigate = useNavigate()
    const audioPlayer = useAudioPlayer()
    const isMobile = useIsMobile()

    const [continueItems, setContinueItems] = useState([])
    const [seriesItems, setSeriesItems] = useState([])
    const [recentItems, setRecentItems] = useState([])
    const [txData, setTxData] = useState(null)
    const [mediaLookup, setMediaLookup] = useState({ ebooks: {}, audiobooks: {} })
    const [loading, setLoading] = useState(true)
    const [readerOpen, setReaderOpen] = useState(null)
    const [playerOpen, setPlayerOpen] = useState(false)
    // The reader installs its pending-save flush here (issue #158), so the
    // Listen handoff can issue the ebook write BEFORE the player's first
    // `source: 'audiobook'` write.
    const readerSaveFlushRef = useRef(null)

    const loadData = useCallback(async () => {
        try {
            const [progress, ebooks, audiobooks, pairs, txQueue] = await Promise.all([
                getAllProgress(),
                getEbooks(),
                getAudiobooks(),
                getPairs(),
                getTranscriptionQueue().catch(() => []),
            ])

            const ebookMap = {}
            ebooks.forEach(e => { ebookMap[e.id] = e })
            const abMap = {}
            audiobooks.forEach(a => { abMap[a.id] = a })
            setMediaLookup({ ebooks: ebookMap, audiobooks: abMap })

            // Pair lookups
            const pairMediaMap = {}
            const ebookToPairId = {}
            const audiobookToPairId = {}
            pairs.forEach(pair => {
                pairMediaMap[pair.id] = { ebookId: pair.ebook?.id, audiobookId: pair.audiobook?.id }
                if (pair.ebook?.id) ebookToPairId[pair.ebook.id] = pair.id
                if (pair.audiobook?.id) audiobookToPairId[pair.audiobook.id] = pair.id
            })

            // --- Build Continue Reading items ---
            const progressItems = progress
                .filter(p => {
                    if (p.is_completed) return false
                    if (p.media_type === 'ebook' && (p.epub_progress_percent > 0 || p.epub_chapter > 0)) return true
                    if (p.media_type === 'audiobook' && p.audio_position_ms > 0) return true
                    return false
                })
                .map(p => {
                    const book = p.media_type === 'ebook' ? ebookMap[p.ebook_id] : abMap[p.audiobook_id]
                    if (!book) return null
                    return { ...p, book }
                })
                .filter(Boolean)

            const pairGroups = {}
            const standaloneItems = []
            progressItems.forEach(p => {
                let resolvedPairId = p.book_pair_id
                if (!resolvedPairId) {
                    if (p.media_type === 'ebook' && p.ebook_id) resolvedPairId = ebookToPairId[p.ebook_id]
                    if (p.media_type === 'audiobook' && p.audiobook_id) resolvedPairId = audiobookToPairId[p.audiobook_id]
                }
                if (resolvedPairId && pairMediaMap[resolvedPairId]) {
                    if (!pairGroups[resolvedPairId]) pairGroups[resolvedPairId] = []
                    pairGroups[resolvedPairId].push(p)
                } else {
                    standaloneItems.push({
                        ...p,
                        itemId: `${p.media_type}_${p.id}`,
                        itemType: p.media_type,
                        mediaId: p.media_type === 'ebook' ? p.ebook_id : p.audiobook_id,
                    })
                }
            })

            const mergedPairItems = Object.entries(pairGroups).map(([pairIdStr, pItems]) => {
                const pairId = Number(pairIdStr)
                const pairInfo = pairMediaMap[pairId]
                const ebookProg = pItems.find(p => p.media_type === 'ebook')
                const audioProg = pItems.find(p => p.media_type === 'audiobook')
                const ebookTime = ebookProg ? new Date(ebookProg.updated_at).getTime() : 0
                const audioTime = audioProg ? new Date(audioProg.updated_at).getTime() : 0
                const lastFormat = ebookTime >= audioTime ? 'ebook' : 'audiobook'
                const primary = lastFormat === 'ebook' ? ebookProg : audioProg
                const book = primary?.book || ebookProg?.book || audioProg?.book

                return {
                    itemId: `pair_${pairId}`,
                    itemType: 'pair',
                    book_pair_id: pairId,
                    lastFormat,
                    book,
                    ebookId: pairInfo.ebookId,
                    audiobookId: pairInfo.audiobookId,
                    ebookProgress: ebookProg ? {
                        percent: ebookProg.epub_progress_percent || 0,
                        chapter: ebookProg.epub_chapter || 0,
                    } : null,
                    audioProgress: audioProg ? { positionMs: audioProg.audio_position_ms || 0 } : null,
                    updated_at: primary?.updated_at || ebookProg?.updated_at || audioProg?.updated_at,
                }
            })

            const allContinue = [...mergedPairItems, ...standaloneItems]
                .sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at))
            setContinueItems(allContinue)

            // --- Build Continue Series items ---
            // Find ebook IDs and audiobook IDs the user has actual progress on
            const inProgressMediaIds = new Set()
            progress.forEach(p => {
                if (p.is_completed) return
                if (p.media_type === 'ebook' && (p.epub_progress_percent > 0 || p.epub_chapter > 0)) {
                    inProgressMediaIds.add(`ebook_${p.ebook_id}`)
                } else if (p.media_type === 'audiobook' && p.audio_position_ms > 0) {
                    inProgressMediaIds.add(`audiobook_${p.audiobook_id}`)
                }
            })

            // Group all books by series
            const seriesMap = {}
            const addToSeries = (book, mediaType) => {
                if (!book.series) return
                const key = book.series
                if (!seriesMap[key]) seriesMap[key] = { name: book.series, author: book.author, books: [] }
                seriesMap[key].books.push({ ...book, mediaType })
            }
            ebooks.forEach(e => addToSeries(e, 'ebook'))
            audiobooks.forEach(a => addToSeries(a, 'audiobook'))

            // For each series, check if user has progress on at least one book
            const seriesInProgress = []
            Object.values(seriesMap).forEach(series => {
                const hasProgress = series.books.some(b =>
                    inProgressMediaIds.has(`${b.mediaType}_${b.id}`)
                )
                if (!hasProgress) return

                // Find next unread book: skip anything completed OR currently in progress.
                // Also skip by series_index so a book that exists as both ebook+audiobook
                // is fully skipped if either version is completed/in-progress.
                const completedIds = new Set(
                    progress.filter(p => p.is_completed).map(p =>
                        p.media_type === 'ebook' ? `ebook_${p.ebook_id}` : `audiobook_${p.audiobook_id}`
                    )
                )
                // Combine completed + in-progress into one skip set
                const skipIds = new Set([...completedIds, ...inProgressMediaIds])
                // Find the highest series_index that is done/active — next book must be above it
                let maxSkippedIndex = -Infinity
                series.books.forEach(b => {
                    if (skipIds.has(`${b.mediaType}_${b.id}`) && b.series_index != null) {
                        if (b.series_index > maxSkippedIndex) maxSkippedIndex = b.series_index
                    }
                })
                const sorted = [...series.books].sort((a, b) => (a.series_index || 0) - (b.series_index || 0))
                const nextBook = sorted.find(b =>
                    !skipIds.has(`${b.mediaType}_${b.id}`) &&
                    b.series_index != null &&
                    b.series_index > maxSkippedIndex
                )
                if (!nextBook) return // all completed or in progress

                seriesInProgress.push({
                    seriesName: series.name,
                    author: series.author,
                    nextBook,
                    seriesIndex: nextBook.series_index,
                })
            })
            setSeriesItems(seriesInProgress)

            // --- Recently Added ---
            const allMedia = [
                ...ebooks.map(e => ({ ...e, mediaType: 'ebook' })),
                ...audiobooks.map(a => ({ ...a, mediaType: 'audiobook' })),
            ]
            allMedia.sort((a, b) => new Date(b.uploaded_at || 0) - new Date(a.uploaded_at || 0))
            setRecentItems(allMedia.slice(0, 15))

            // --- Transcription Status ---
            const queuedPairIds = new Set(txQueue.map(q => q.book_pair_id))
            const activeItem = txQueue.find(q => q.status === 'in_progress')

            const txCounts = { transcribed: 0, notTranscribed: 0, inProgress: 0, queued: 0 }
            txQueue.forEach(q => {
                if (q.status === 'in_progress') txCounts.inProgress++
                else if (q.status === 'pending') txCounts.queued++
            })
            pairs.forEach(p => {
                if (queuedPairIds.has(p.id)) return
                if (p.status === 'synced') txCounts.transcribed++
                else if (p.status !== 'transcribing') txCounts.notTranscribed++
            })
            setTxData({ counts: txCounts, activeItem })

        } catch (err) {
            console.error('Failed to load home data:', err)
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => { loadData() }, [loadData])

    // --- Actions ---

    // Device attribution + write-ordering fields sent with every progress
    // write (issue #54). captured_at is read fresh per-call so a batch of
    // Promise.all writes each stamp their own moment.
    const deviceMeta = () => ({
        device_id: getDeviceId(),
        device_name: getDeviceName(),
        captured_at: new Date().toISOString(),
    })

    const handleMarkComplete = async (item) => {
        // Optimistic removal — instant feedback
        setContinueItems(prev => prev.filter(i => i.itemId !== item.itemId))
        try {
            // One write for a pair: both halves share a single canonical
            // record, so the two per-media writes this replaced could be
            // adjudicated separately and leave the book half-complete.
            if (item.itemType === 'pair') {
                await updatePosition('pair', item.book_pair_id, { is_completed: true, ...deviceMeta() })
            } else {
                await updatePosition(item.itemType, item.mediaId, { is_completed: true, ...deviceMeta() })
            }
        } catch (err) {
            console.error('Failed to mark complete:', err)
        } finally {
            loadData()
        }
    }

    const handleResetProgress = async (item) => {
        // Optimistic removal — instant feedback
        setContinueItems(prev => prev.filter(i => i.itemId !== item.itemId))
        try {
            // Scope-level DELETE in both cases: it removes the canonical
            // record, its hints and the projection. The zero-write the
            // standalone branches used to do left the record in place, so the
            // next save resurrected the position.
            if (item.itemType === 'pair') {
                await resetPairProgress(item.book_pair_id)
            } else {
                await resetPosition(item.itemType, item.mediaId)
            }
        } catch (err) {
            console.error('Failed to reset progress:', err)
        } finally {
            // Sync true server state
            loadData()
        }
    }

    // --- Reader / Player helpers ---

    // No CFI is threaded through: EbookReader fetches the canonical position
    // itself at open, which is fresher than any snapshot this page holds.
    const openReader = (ebookId, pairId, chapter, title, audiobookId) => {
        const audiobook = mediaLookup.audiobooks[audiobookId] || null
        setReaderOpen({
            ebookId, pairId: pairId || null, chapter: chapter || null,
            title, pairedAudiobookId: audiobookId || null,
            pairedAudiobook: audiobook ? { ...audiobook, pair_id: pairId } : null,
        })
    }

    const openPlayer = (audiobookId, pairId, positionMs, ebookId) => {
        const audiobook = mediaLookup.audiobooks[audiobookId]
        if (!audiobook) return
        audioPlayer.play(audiobookId, { ...audiobook, pair_id: pairId }, positionMs || 0, ebookId || null)
        setPlayerOpen(true)
    }

    const handleContinue = (item) => {
        if (item.itemType === 'pair') {
            if (item.lastFormat === 'ebook' && item.ebookId) {
                const eb = item.ebookProgress
                openReader(item.ebookId, item.book_pair_id, eb?.chapter, item.book?.title, item.audiobookId)
            } else if (item.audiobookId) {
                openPlayer(item.audiobookId, item.book_pair_id, item.audioProgress?.positionMs, item.ebookId)
            }
        } else if (item.itemType === 'ebook') {
            if (item.book?.format === 'epub') {
                openReader(item.mediaId, item.book_pair_id, item.epub_chapter, item.book?.title, null)
            } else {
                navigate(`/book/ebook/${item.mediaId}`)
            }
        } else {
            openPlayer(item.mediaId, item.book_pair_id, item.audio_position_ms, null)
        }
    }

    const handleRead = (item) => {
        if (item.itemType === 'pair') {
            const eb = item.ebookProgress
            openReader(item.ebookId, item.book_pair_id, eb?.chapter, item.book?.title, item.audiobookId)
        } else if (item.itemType === 'ebook') {
            openReader(item.mediaId, item.book_pair_id, item.epub_chapter, item.book?.title, null)
        }
    }

    const handleListen = (item) => {
        if (item.itemType === 'pair') {
            openPlayer(item.audiobookId, item.book_pair_id, item.audioProgress?.positionMs, item.ebookId)
        } else if (item.itemType === 'audiobook') {
            openPlayer(item.mediaId, item.book_pair_id, item.audio_position_ms, null)
        }
    }

    // --- Overlay views ---

    if (readerOpen) {
        return (
            <EbookReader
                ebookId={readerOpen.ebookId}
                pairId={readerOpen.pairId}
                initialChapter={readerOpen.chapter != null && readerOpen.chapter >= 0 ? readerOpen.chapter : null}
                initialTextPreview={readerOpen.textPreview || null}
                bookTitle={readerOpen.title}
                saveFlushRef={readerSaveFlushRef}
                onClose={() => { setReaderOpen(null); loadData() }}
                onSwitchToAudio={readerOpen.pairedAudiobookId ? async () => {
                    // Issue the reader's pending position write first —
                    // otherwise the handoff drops the last page turn and the
                    // player's `source: 'audiobook'` write reopens the pair
                    // at a stale text position (issue #158).
                    readerSaveFlushRef.current?.()
                    const pos = await getPosition('pair', readerOpen.pairId).catch(() => null)
                    let audioPositionMs = pos?.audio_position_ms || 0
                    if (!audioPositionMs) {
                        const prog = await apiGetProgress('audiobook', readerOpen.pairedAudiobookId).catch(() => null)
                        audioPositionMs = prog?.audio_position_ms || 0
                    }
                    setReaderOpen(null)
                    openPlayer(readerOpen.pairedAudiobookId, readerOpen.pairId, audioPositionMs, readerOpen.ebookId)
                } : null}
            />
        )
    }

    if (playerOpen && audioPlayer.currentAudiobook) {
        return (
            <AudioPlayerView
                onClose={() => { setPlayerOpen(false); loadData() }}
                onSwitchToEbook={audioPlayer.pairedEbookId ? async (pairId) => {
                    audioPlayer.pause()
                    const posMs = Math.floor(audioPlayer.currentTime * 1000)
                    const pos = await updatePosition('pair', pairId, {
                        source: 'audiobook', audio_position_ms: posMs, ...deviceMeta(),
                    }).catch(() => null)
                    setPlayerOpen(false)
                    const ebookBook = mediaLookup.ebooks[audioPlayer.pairedEbookId]
                    setReaderOpen({
                        ebookId: audioPlayer.pairedEbookId,
                        pairId,
                        chapter: pos?.epub_chapter ?? null,
                        textPreview: pos?.epub_text_preview ?? null,
                        title: ebookBook?.title || 'Reading',
                        pairedAudiobookId: audioPlayer.currentAudiobook.id,
                        pairedAudiobook: { ...audioPlayer.currentAudiobook, pair_id: pairId },
                    })
                } : null}
            />
        )
    }

    // --- Main view ---

    if (loading) {
        return (
            <div className="loading-page" style={{ minHeight: '60vh' }}>
                <div className="spinner"></div>
                <span>Loading...</span>
            </div>
        )
    }

    return (
        <div className="home-page">

            {/* ── Welcome Hero (mobile only) ── */}
            {isMobile && (
                <div className="home-hero">
                    <div className="home-hero-label">Your Library</div>
                    <h1 className="home-hero-title">Welcome back.</h1>
                </div>
            )}

            {/* ── Transcription Status ── */}
            {txData && (
                <section className="home-section">
                    <div
                        className="home-tx-card"
                        onClick={() => navigate('/transcription/not-transcribed')}
                        role="button"
                        tabIndex={0}
                        onKeyDown={e => e.key === 'Enter' && navigate('/transcription/not-transcribed')}
                    >
                        <div className="home-tx-header">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="16" height="16">
                                <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
                                <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                                <line x1="12" y1="19" x2="12" y2="23" />
                                <line x1="8" y1="23" x2="16" y2="23" />
                            </svg>
                            <span className="home-tx-title">Transcription Status</span>
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="14" height="14" className="home-tx-chevron">
                                <polyline points="9 18 15 12 9 6" />
                            </svg>
                        </div>
                        <div className="home-tx-counts">
                            {txData.counts.inProgress > 0 && (
                                <span className="home-tx-dot in-progress">{txData.counts.inProgress} In Progress</span>
                            )}
                            {txData.counts.queued > 0 && (
                                <span className="home-tx-dot queued">{txData.counts.queued} Queued</span>
                            )}
                            <span className="home-tx-dot transcribed">{txData.counts.transcribed} Transcribed</span>
                            <span className="home-tx-dot not-transcribed">{txData.counts.notTranscribed} Not Transcribed</span>
                        </div>
                        {/* Mobile: 3-column stat blocks */}
                        {isMobile && (
                            <div className="home-tx-stats-mobile">
                                <div className="home-tx-stat-block">
                                    <div className="home-tx-stat-dot queued" />
                                    <span className="home-tx-stat-number">{String(txData.counts.queued).padStart(2, '0')}</span>
                                    <span className="home-tx-stat-label">Queued</span>
                                </div>
                                <div className="home-tx-stat-block">
                                    <div className="home-tx-stat-dot active" />
                                    <span className="home-tx-stat-number">{String(txData.counts.inProgress).padStart(2, '0')}</span>
                                    <span className="home-tx-stat-label">Active</span>
                                </div>
                                <div className="home-tx-stat-block">
                                    <div className="home-tx-stat-dot done" />
                                    <span className="home-tx-stat-number">{String(txData.counts.transcribed).padStart(2, '0')}</span>
                                    <span className="home-tx-stat-label">Done</span>
                                </div>
                            </div>
                        )}
                        {txData.activeItem && (
                            <div className="home-tx-active">
                                <div className="home-tx-active-label">
                                    {txData.activeItem.book_title} — {Math.round((txData.activeItem.progress || 0) * 100)}%
                                </div>
                                <div className="home-tx-active-bar">
                                    <div
                                        className="home-tx-active-fill"
                                        style={{ width: `${Math.round((txData.activeItem.progress || 0) * 100)}%` }}
                                    />
                                </div>
                            </div>
                        )}
                    </div>
                </section>
            )}

            {/* ── Continue Reading/Listening ── */}
            <section className="home-section">
                <div className="home-section-header">
                    <h3>Continue Reading</h3>
                    {isMobile && continueItems.length > 0 && (
                        <a className="home-view-all" href="#" onClick={e => { e.preventDefault(); navigate('/library') }}>VIEW ALL</a>
                    )}
                </div>
                {continueItems.length === 0 ? (
                    <div className="home-empty">Nothing in progress — start reading!</div>
                ) : (
                    <Carousel>
                        {continueItems.map(item => {
                            const { value: progressVal } = calcProgress(item, mediaLookup.audiobooks)
                            const isPair = item.itemType === 'pair'
                            const isEbook = item.itemType === 'ebook'
                            const canRead = isPair ? !!item.ebookId : isEbook
                            const canListen = isPair ? !!item.audiobookId : !isEbook

                            return (
                                <BookCard
                                    key={item.itemId}
                                    book={item.book}
                                    size="continue"
                                    progress={progressVal}
                                    isPair={isPair}
                                    isEbook={isEbook}
                                    onPrimary={() => handleContinue(item)}
                                    onRead={canRead ? () => handleRead(item) : null}
                                    onListen={canListen ? () => handleListen(item) : null}
                                    onMarkComplete={() => handleMarkComplete(item)}
                                    onResetProgress={() => handleResetProgress(item)}
                                    onViewDetails={() => navigate(isEbook || isPair
                                        ? `/book/ebook/${item.ebookId || item.mediaId}`
                                        : `/book/audiobook/${item.mediaId}`
                                    )}
                                />
                            )
                        })}
                    </Carousel>
                )}
            </section>

            {/* ── Continue Series ── */}
            {seriesItems.length > 0 && (
                <section className="home-section">
                    <div className="home-section-header">
                        <h3>Continue Series</h3>
                    </div>
                    <Carousel>
                        {seriesItems.map(s => {
                            const book = s.nextBook

                            if (isMobile) {
                                return (
                                    <div
                                        key={s.seriesName}
                                        className="home-series-card-mobile"
                                        onClick={() => navigate('/series')}
                                    >
                                        {book?.cover_path ? (
                                            <CoverImg path={book?.cover_path} alt={s.seriesName} loading="lazy" />
                                        ) : (
                                            <div className="series-card-placeholder">
                                                <span>📖</span>
                                            </div>
                                        )}
                                        <div className="series-card-gradient" />
                                        <div className="series-card-title">{s.seriesName}</div>
                                        {s.seriesIndex != null && (
                                            <div className="series-card-badge">BK {Math.round(s.seriesIndex)}</div>
                                        )}
                                    </div>
                                )
                            }

                            return (
                                <div
                                    key={s.seriesName}
                                    className="home-book-card continue-size"
                                    onClick={() => navigate('/series')}
                                >
                                    <div className="home-book-card-cover">
                                        {book?.cover_path ? (
                                            <CoverImg path={book?.cover_path} alt={book?.title} loading="lazy" />
                                        ) : (
                                            <div className="home-book-card-placeholder">
                                                <span>📖</span>
                                            </div>
                                        )}
                                        {s.seriesIndex != null && (
                                            <div className="home-book-card-badge">#{Math.round(s.seriesIndex)}</div>
                                        )}
                                    </div>
                                    <div className="home-book-card-title" title={s.seriesName}>{s.seriesName}</div>
                                    {s.author && <div className="home-book-card-author">{s.author}</div>}
                                </div>
                            )
                        })}
                    </Carousel>
                </section>
            )}

            {/* ── Recently Added ── */}
            {recentItems.length > 0 && (
                <section className="home-section">
                    <div className="home-section-header">
                        <h3>Recently Added</h3>
                    </div>
                    <Carousel>
                        {recentItems.map(book => {
                            if (isMobile) {
                                return (
                                    <div
                                        key={`${book.mediaType}_${book.id}`}
                                        className="home-recent-card-mobile"
                                        onClick={() => navigate(`/book/${book.mediaType}/${book.id}`)}
                                    >
                                        <div className="recent-card-thumb">
                                            {book.cover_path ? (
                                                <CoverImg path={book.cover_path} alt={book.title} loading="lazy" />
                                            ) : (
                                                <div className="recent-card-thumb-placeholder">
                                                    <span>{book.mediaType === 'ebook' ? '📚' : '🎧'}</span>
                                                </div>
                                            )}
                                        </div>
                                        <div className="recent-card-info">
                                            <div className="recent-card-title">{book.title}</div>
                                            {book.author && <div className="recent-card-author">{book.author}</div>}
                                        </div>
                                    </div>
                                )
                            }

                            return (
                                <div
                                    key={`${book.mediaType}_${book.id}`}
                                    className="home-book-card recent-size"
                                    onClick={() => navigate(`/book/${book.mediaType}/${book.id}`)}
                                >
                                    <div className="home-book-card-cover">
                                        {book.cover_path ? (
                                            <CoverImg path={book.cover_path} alt={book.title} loading="lazy" />
                                        ) : (
                                            <div className="home-book-card-placeholder">
                                                <span>{book.mediaType === 'ebook' ? '📚' : '🎧'}</span>
                                            </div>
                                        )}
                                    </div>
                                    <div className="home-book-card-title" title={book.title}>{book.title}</div>
                                    {book.author && <div className="home-book-card-author">{book.author}</div>}
                                </div>
                            )
                        })}
                    </Carousel>
                </section>
            )}
        </div>
    )
}

export default HomePage
