import React, { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { getAllProgress, getEbooks, getAudiobooks, getPairs, updateProgress, getProgress, getBookmark, updateBookmark, getAccessToken, getDeviceId, getDeviceName } from '../api'
import { useAudioPlayer } from '../contexts/AudioPlayerContext'
import EbookReader from '../components/EbookReader'
import { AudioPlayerView } from '../components/AudioPlayer'

function formatTime(ms) {
    if (!ms) return '0:00'
    const totalSec = Math.floor(ms / 1000)
    const h = Math.floor(totalSec / 3600)
    const m = Math.floor((totalSec % 3600) / 60)
    const s = totalSec % 60
    if (h > 0) return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
    return `${m}:${s.toString().padStart(2, '0')}`
}

function ContinuePage() {
    const navigate = useNavigate()
    const audioPlayer = useAudioPlayer()

    const [items, setItems] = useState([])
    const [mediaLookup, setMediaLookup] = useState({ ebooks: {}, audiobooks: {} })
    const [loading, setLoading] = useState(true)
    const [menuOpen, setMenuOpen] = useState(null)
    const [readerOpen, setReaderOpen] = useState(null)
    const [playerOpen, setPlayerOpen] = useState(false)

    const loadData = useCallback(async () => {
        try {
            const [progress, ebooks, audiobooks, pairs] = await Promise.all([
                getAllProgress(),
                getEbooks(),
                getAudiobooks(),
                getPairs(),
            ])

            const ebookMap = {}
            ebooks.forEach(e => { ebookMap[e.id] = e })
            const abMap = {}
            audiobooks.forEach(a => { abMap[a.id] = a })
            setMediaLookup({ ebooks: ebookMap, audiobooks: abMap })

            // Build pair lookup from pairs list (authoritative source for paired IDs)
            const pairMediaMap = {}
            const ebookToPairId = {}
            const audiobookToPairId = {}
            pairs.forEach(pair => {
                pairMediaMap[pair.id] = {
                    ebookId: pair.ebook.id,
                    audiobookId: pair.audiobook.id,
                    ebook: pair.ebook,
                    audiobook: pair.audiobook,
                }
                if (pair.ebook?.id) ebookToPairId[pair.ebook.id] = pair.id
                if (pair.audiobook?.id) audiobookToPairId[pair.audiobook.id] = pair.id
            })

            // Build progress items (only items with actual progress)
            const progressItems = progress
                .filter(p => {
                    if (p.is_completed) return false
                    if (p.media_type === 'ebook' && (p.epub_progress_percent > 0 || p.epub_chapter > 0)) return true
                    if (p.media_type === 'audiobook' && p.audio_position_ms > 0) return true
                    return false
                })
                .map(p => {
                    const book = p.media_type === 'ebook'
                        ? ebookMap[p.ebook_id]
                        : abMap[p.audiobook_id]
                    if (!book) return null
                    return { ...p, book }
                })
                .filter(Boolean)

            // Consolidate: merge ebook + audiobook progress for the same pair into one item
            const pairGroups = {}
            const standaloneItems = []

            progressItems.forEach(p => {
                // Resolve pair ID from book_pair_id or via reverse lookup from media ID
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

                // Default to whichever format was used most recently
                const ebookTime = ebookProg ? new Date(ebookProg.updated_at).getTime() : 0
                const audioTime = audioProg ? new Date(audioProg.updated_at).getTime() : 0
                const lastFormat = ebookTime > audioTime ? 'ebook' : 'audiobook'
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
                        cfi: ebookProg.epub_cfi || null,
                        chapter: ebookProg.epub_chapter || 0,
                    } : null,
                    audioProgress: audioProg ? {
                        positionMs: audioProg.audio_position_ms || 0,
                    } : null,
                    updated_at: primary?.updated_at || ebookProg?.updated_at || audioProg?.updated_at,
                }
            })

            const allItems = [...mergedPairItems, ...standaloneItems]
                .sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at))

            setItems(allItems)
        } catch (err) {
            console.error('Failed to load continue data:', err)
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
        setMenuOpen(null)
        try {
            if (item.itemType === 'pair') {
                await Promise.all([
                    item.ebookId ? updateProgress('ebook', item.ebookId, { is_completed: true, ...deviceMeta() }).catch(() => {}) : null,
                    item.audiobookId ? updateProgress('audiobook', item.audiobookId, { is_completed: true, ...deviceMeta() }).catch(() => {}) : null,
                ].filter(Boolean))
            } else {
                await updateProgress(item.itemType, item.mediaId, { is_completed: true, ...deviceMeta() })
            }
            setItems(prev => prev.filter(i => i.itemId !== item.itemId))
        } catch (err) {
            console.error('Failed to mark complete:', err)
        }
    }

    const handleResetProgress = async (item) => {
        setMenuOpen(null)
        try {
            if (item.itemType === 'pair') {
                await Promise.all([
                    item.ebookId ? updateProgress('ebook', item.ebookId, {
                        is_completed: false, ...deviceMeta(),
                        epub_progress_percent: 0, epub_cfi: '', epub_chapter: 0,
                    }).catch(() => {}) : null,
                    item.audiobookId ? updateProgress('audiobook', item.audiobookId, {
                        is_completed: false, ...deviceMeta(),
                        audio_position_ms: 0,
                    }).catch(() => {}) : null,
                ].filter(Boolean))
            } else if (item.itemType === 'ebook') {
                await updateProgress('ebook', item.mediaId, {
                    is_completed: false, ...deviceMeta(),
                    epub_progress_percent: 0, epub_cfi: '', epub_chapter: 0,
                })
            } else {
                await updateProgress('audiobook', item.mediaId, {
                    is_completed: false, ...deviceMeta(),
                    audio_position_ms: 0,
                })
            }
            setItems(prev => prev.filter(i => i.itemId !== item.itemId))
        } catch (err) {
            console.error('Failed to reset progress:', err)
        }
    }

    // --- Navigation helpers ---

    const openReader = (ebookId, pairId, cfi, chapter, title, audiobookId) => {
        const audiobook = mediaLookup.audiobooks[audiobookId] || null
        setReaderOpen({
            ebookId,
            pairId: pairId || null,
            cfi: cfi || null,
            chapter: chapter || null,
            title,
            pairedAudiobookId: audiobookId || null,
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
                openReader(item.ebookId, item.book_pair_id, eb?.cfi, eb?.chapter, item.book.title, item.audiobookId)
            } else if (item.audiobookId) {
                openPlayer(item.audiobookId, item.book_pair_id, item.audioProgress?.positionMs, item.ebookId)
            }
        } else if (item.itemType === 'ebook') {
            if (item.book.format === 'epub') {
                openReader(item.mediaId, item.book_pair_id, item.epub_cfi, item.epub_chapter, item.book.title, null)
            } else {
                navigate(`/book/ebook/${item.mediaId}`)
            }
        } else {
            openPlayer(item.mediaId, item.book_pair_id, item.audio_position_ms, null)
        }
    }

    const handleRead = (item) => {
        setMenuOpen(null)
        if (item.itemType === 'pair') {
            const eb = item.ebookProgress
            openReader(item.ebookId, item.book_pair_id, eb?.cfi, eb?.chapter, item.book.title, item.audiobookId)
        } else if (item.itemType === 'ebook') {
            openReader(item.mediaId, item.book_pair_id, item.epub_cfi, item.epub_chapter, item.book.title, null)
        }
    }

    const handleListen = (item) => {
        setMenuOpen(null)
        if (item.itemType === 'pair') {
            openPlayer(item.audiobookId, item.book_pair_id, item.audioProgress?.positionMs, item.ebookId)
        } else if (item.itemType === 'audiobook') {
            openPlayer(item.mediaId, item.book_pair_id, item.audio_position_ms, null)
        }
    }

    // Close click-away for menus
    useEffect(() => {
        if (menuOpen !== null) {
            const handler = () => setMenuOpen(null)
            window.addEventListener('click', handler)
            return () => window.removeEventListener('click', handler)
        }
    }, [menuOpen])

    // --- Overlay views ---

    if (readerOpen) {
        return (
            <EbookReader
                ebookId={readerOpen.ebookId}
                pairId={readerOpen.pairId}
                initialCfi={readerOpen.cfi || null}
                initialChapter={!readerOpen.cfi && readerOpen.chapter != null && readerOpen.chapter >= 0 ? readerOpen.chapter : null}
                initialTextPreview={readerOpen.textPreview || null}
                bookTitle={readerOpen.title}
                onClose={() => { setReaderOpen(null); loadData() }}
                onSwitchToAudio={readerOpen.pairedAudiobookId ? async () => {
                    console.log(`[ContinuePage] onSwitchToAudio: pairId=${readerOpen.pairId}, audiobookId=${readerOpen.pairedAudiobookId}`)
                    const bm = await getBookmark(readerOpen.pairId).catch(() => null)
                    console.log(`[ContinuePage] bookmark:`, bm)
                    let audioPositionMs = bm?.audio_position_ms || 0
                    if (!audioPositionMs) {
                        const prog = await getProgress('audiobook', readerOpen.pairedAudiobookId).catch(() => null)
                        console.log(`[ContinuePage] fallback progress:`, prog)
                        audioPositionMs = prog?.audio_position_ms || 0
                    }
                    console.log(`[ContinuePage] opening player at ${audioPositionMs}ms`)
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
                    console.log(`[ContinuePage] onSwitchToEbook: pairId=${pairId}, ebookId=${audioPlayer.pairedEbookId}, audioTime=${audioPlayer.currentTime}s`)
                    audioPlayer.pause()
                    const posMs = Math.floor(audioPlayer.currentTime * 1000)
                    console.log(`[ContinuePage] updating bookmark: audioPos=${posMs}ms`)
                    await updateBookmark(pairId, { source: 'audiobook', audio_position_ms: posMs }).catch(e => console.warn('updateBookmark failed:', e))
                    const bm = await getBookmark(pairId).catch(e => { console.warn('getBookmark failed:', e); return null })
                    console.log(`[ContinuePage] bookmark after update:`, bm)
                    setPlayerOpen(false)
                    const ebookBook = mediaLookup.ebooks[audioPlayer.pairedEbookId]
                    const chapter = bm?.epub_chapter ?? null
                    const textPreview = bm?.epub_text_preview ?? null
                    console.log(`[ContinuePage] opening ebook at chapter=${chapter}, textPreview='${textPreview?.substring(0, 60)}', ebookId=${audioPlayer.pairedEbookId}`)
                    setReaderOpen({
                        ebookId: audioPlayer.pairedEbookId,
                        pairId,
                        cfi: null,
                        chapter,
                        textPreview,
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
        <div style={{ padding: '0' }}>
            <div className="page-header">
                <h2>Continue</h2>
                <p style={{ color: 'var(--text-secondary)', fontSize: 14, marginTop: 4 }}>
                    Pick up where you left off
                </p>
            </div>

            {items.length === 0 ? (
                <div className="empty-state">
                    <div className="icon" style={{ fontSize: 48 }}>📖</div>
                    <h3>Nothing in progress</h3>
                    <p>Start reading an ebook or listening to an audiobook to see it here.</p>
                </div>
            ) : (
                <div className="continue-grid">
                    {items.map(item => {
                        const isPair = item.itemType === 'pair'
                        const isEbook = item.itemType === 'ebook'

                        let progressValue, progressLabel
                        if (isPair) {
                            if (item.lastFormat === 'audiobook' && item.audioProgress) {
                                const durSec = mediaLookup.audiobooks[item.audiobookId]?.duration_seconds
                                progressValue = durSec ? ((item.audioProgress.positionMs / 1000) / durSec) * 100 : 0
                                progressLabel = formatTime(item.audioProgress.positionMs)
                            } else if (item.ebookProgress) {
                                progressValue = item.ebookProgress.percent
                                progressLabel = `${Math.round(item.ebookProgress.percent)}%`
                            } else if (item.audioProgress) {
                                const durSec = mediaLookup.audiobooks[item.audiobookId]?.duration_seconds
                                progressValue = durSec ? ((item.audioProgress.positionMs / 1000) / durSec) * 100 : 0
                                progressLabel = formatTime(item.audioProgress.positionMs)
                            } else {
                                progressValue = 0
                                progressLabel = ''
                            }
                        } else if (isEbook) {
                            progressValue = item.epub_progress_percent || 0
                            progressLabel = `${Math.round(item.epub_progress_percent || 0)}%`
                        } else {
                            const durSec = item.book.duration_seconds
                            progressValue = durSec ? ((item.audio_position_ms / 1000) / durSec) * 100 : 0
                            progressLabel = formatTime(item.audio_position_ms)
                        }

                        const coverUrl = item.book.cover_path
                            ? `${item.book.cover_path}?token=${getAccessToken()}`
                            : null

                        return (
                            <div key={item.itemId} className="continue-card" onClick={() => handleContinue(item)}>
                                <div className="continue-card-cover">
                                    {coverUrl ? (
                                        <img src={coverUrl} alt={item.book.title} />
                                    ) : (
                                        <div className="continue-card-cover-placeholder">
                                            <span>{isPair ? '📖' : isEbook ? '📚' : '🎧'}</span>
                                        </div>
                                    )}
                                    <div className="continue-card-badge">
                                        {isPair ? (
                                            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2">
                                                <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
                                                <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
                                            </svg>
                                        ) : isEbook ? '📚' : '🎧'}
                                    </div>
                                </div>
                                <div className="continue-card-info">
                                    <div className="continue-card-title">{item.book.title}</div>
                                    {item.book.author && (
                                        <div className="continue-card-author">{item.book.author}</div>
                                    )}
                                    <div className="continue-card-progress">
                                        <div className="continue-card-progress-bar">
                                            <div
                                                className="continue-card-progress-fill"
                                                style={{ width: `${Math.min(progressValue, 100)}%` }}
                                            />
                                        </div>
                                        <span className="continue-card-progress-label">{progressLabel}</span>
                                    </div>
                                </div>
                                <div className="continue-card-menu" onClick={e => { e.stopPropagation(); setMenuOpen(menuOpen === item.itemId ? null : item.itemId) }}>
                                    <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
                                        <circle cx="12" cy="5" r="1.5" /><circle cx="12" cy="12" r="1.5" /><circle cx="12" cy="19" r="1.5" />
                                    </svg>
                                    {menuOpen === item.itemId && (
                                        <div className="continue-card-dropdown">
                                            {(isPair ? !!item.ebookId : isEbook) && (
                                                <button onClick={(e) => { e.stopPropagation(); handleRead(item) }}>
                                                    Continue Reading
                                                </button>
                                            )}
                                            {(isPair ? !!item.audiobookId : !isEbook) && (
                                                <button onClick={(e) => { e.stopPropagation(); handleListen(item) }}>
                                                    Continue Listening
                                                </button>
                                            )}
                                            <button onClick={(e) => { e.stopPropagation(); handleMarkComplete(item) }}>
                                                Mark Complete
                                            </button>
                                            <button onClick={(e) => { e.stopPropagation(); handleResetProgress(item) }}>
                                                Reset Progress
                                            </button>
                                            <button onClick={(e) => { e.stopPropagation(); setMenuOpen(null); navigate(isEbook || isPair ? `/book/ebook/${item.ebookId || item.mediaId}` : `/book/audiobook/${item.mediaId}`) }}>
                                                View Details
                                            </button>
                                        </div>
                                    )}
                                </div>
                            </div>
                        )
                    })}
                </div>
            )}
        </div>
    )
}

export default ContinuePage
