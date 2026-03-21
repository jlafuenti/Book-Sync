import React, { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { getAllProgress, getEbooks, getAudiobooks, updateProgress, getProgress, getBookmark, getAccessToken } from '../api'
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
    const [loading, setLoading] = useState(true)
    const [menuOpen, setMenuOpen] = useState(null) // item id
    const [readerOpen, setReaderOpen] = useState(null) // { ebookId, pairId, cfi, title }
    const [playerOpen, setPlayerOpen] = useState(false)

    const loadData = useCallback(async () => {
        try {
            const [progress, ebooks, audiobooks] = await Promise.all([
                getAllProgress(),
                getEbooks(),
                getAudiobooks(),
            ])

            const ebookMap = {}
            ebooks.forEach(e => { ebookMap[e.id] = e })
            const abMap = {}
            audiobooks.forEach(a => { abMap[a.id] = a })

            // Build a lookup: book_pair_id → { ebookId, audiobookId } from progress records
            const pairMediaMap = {}
            progress.forEach(p => {
                if (p.book_pair_id) {
                    if (!pairMediaMap[p.book_pair_id]) pairMediaMap[p.book_pair_id] = {}
                    if (p.media_type === 'ebook') pairMediaMap[p.book_pair_id].ebookId = p.ebook_id
                    if (p.media_type === 'audiobook') pairMediaMap[p.book_pair_id].audiobookId = p.audiobook_id
                }
            })

            const continueItems = progress
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
                    const pairInfo = p.book_pair_id ? pairMediaMap[p.book_pair_id] : null
                    return {
                        ...p,
                        book,
                        mediaId: p.media_type === 'ebook' ? p.ebook_id : p.audiobook_id,
                        pairedEbookId: pairInfo?.ebookId || null,
                        pairedAudiobookId: pairInfo?.audiobookId || null,
                    }
                })
                .filter(Boolean)
                .sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at))

            setItems(continueItems)
        } catch (err) {
            console.error('Failed to load continue data:', err)
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => { loadData() }, [loadData])

    const handleMarkComplete = async (item) => {
        setMenuOpen(null)
        try {
            await updateProgress(item.media_type, item.mediaId, {
                is_completed: true,
                device_id: 'web',
            })
            setItems(prev => prev.filter(i => i.id !== item.id))
        } catch (err) {
            console.error('Failed to mark complete:', err)
        }
    }

    const handleResetProgress = async (item) => {
        setMenuOpen(null)
        try {
            const resetData = {
                is_completed: false,
                device_id: 'web',
            }
            if (item.media_type === 'ebook') {
                resetData.epub_progress_percent = 0
                resetData.epub_cfi = ''
                resetData.epub_chapter = 0
            } else {
                resetData.audio_position_ms = 0
            }
            await updateProgress(item.media_type, item.mediaId, resetData)
            setItems(prev => prev.filter(i => i.id !== item.id))
        } catch (err) {
            console.error('Failed to reset progress:', err)
        }
    }

    const handleContinue = (item) => {
        if (item.media_type === 'ebook') {
            if (item.book.format === 'epub') {
                setReaderOpen({
                    ebookId: item.mediaId,
                    pairId: item.book_pair_id || null,
                    cfi: item.epub_cfi || null,
                    chapter: item.epub_chapter || null,
                    title: item.book.title,
                    pairedAudiobookId: item.pairedAudiobookId,
                    pairedAudiobook: item.pairedAudiobookId ? abMap[item.pairedAudiobookId] : null,
                })
            } else {
                navigate(`/book/ebook/${item.mediaId}`)
            }
        } else {
            audioPlayer.play(item.mediaId, item.book, item.audio_position_ms || 0, item.pairedEbookId)
            setPlayerOpen(true)
        }
    }

    // Keep abMap accessible for handleContinue — build it once items are loaded
    const abMap = Object.fromEntries(items.map(i => i.media_type === 'audiobook' ? [i.mediaId, i.book] : []).filter(e => e.length))

    // Close click-away for menus
    useEffect(() => {
        if (menuOpen !== null) {
            const handler = () => setMenuOpen(null)
            window.addEventListener('click', handler)
            return () => window.removeEventListener('click', handler)
        }
    }, [menuOpen])

    if (readerOpen) {
        return (
            <EbookReader
                ebookId={readerOpen.ebookId}
                pairId={readerOpen.pairId}
                initialCfi={readerOpen.cfi || null}
                initialChapter={!readerOpen.cfi && readerOpen.chapter > 0 ? readerOpen.chapter : null}
                bookTitle={readerOpen.title}
                onClose={() => { setReaderOpen(null); loadData() }}
                onSwitchToAudio={readerOpen.pairedAudiobookId ? async () => {
                    const bm = await getBookmark(readerOpen.pairId).catch(() => null)
                    let audioPositionMs = bm?.audio_position_ms || 0
                    if (!audioPositionMs) {
                        const prog = await getProgress('audiobook', readerOpen.pairedAudiobookId).catch(() => null)
                        audioPositionMs = prog?.audio_position_ms || 0
                    }
                    setReaderOpen(null)
                    audioPlayer.play(
                        readerOpen.pairedAudiobookId,
                        readerOpen.pairedAudiobook,
                        audioPositionMs,
                        readerOpen.ebookId
                    )
                    setPlayerOpen(true)
                } : null}
            />
        )
    }

    if (playerOpen && audioPlayer.currentAudiobook) {
        return (
            <AudioPlayerView
                onClose={() => setPlayerOpen(false)}
                onSwitchToEbook={audioPlayer.pairedEbookId ? async (pairId) => {
                    const bm = await getBookmark(pairId).catch(() => null)
                    audioPlayer.pause()
                    setPlayerOpen(false)
                    const ebookBook = items.find(i => i.media_type === 'ebook' && i.mediaId === audioPlayer.pairedEbookId)
                    setReaderOpen({
                        ebookId: audioPlayer.pairedEbookId,
                        pairId,
                        cfi: null,
                        chapter: bm?.epub_chapter ?? null,
                        title: ebookBook?.book?.title || 'Reading',
                        pairedAudiobookId: audioPlayer.currentAudiobook.id,
                        pairedAudiobook: audioPlayer.currentAudiobook,
                    })
                } : null}
            />
        )
    }

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
                        const isEbook = item.media_type === 'ebook'
                        const progressValue = isEbook
                            ? (item.epub_progress_percent || 0)
                            : (item.book.duration_seconds
                                ? ((item.audio_position_ms / 1000) / item.book.duration_seconds) * 100
                                : 0)
                        const progressLabel = isEbook
                            ? `${Math.round(item.epub_progress_percent || 0)}%`
                            : formatTime(item.audio_position_ms)

                        const coverUrl = item.book.cover_path
                            ? `/api/files/covers/${item.book.cover_path}?token=${getAccessToken()}`
                            : null

                        return (
                            <div key={item.id} className="continue-card" onClick={() => handleContinue(item)}>
                                <div className="continue-card-cover">
                                    {coverUrl ? (
                                        <img src={coverUrl} alt={item.book.title} />
                                    ) : (
                                        <div className="continue-card-cover-placeholder">
                                            <span>{isEbook ? '📚' : '🎧'}</span>
                                        </div>
                                    )}
                                    <div className="continue-card-badge">
                                        {isEbook ? '📚' : '🎧'}
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
                                <div className="continue-card-menu" onClick={e => { e.stopPropagation(); setMenuOpen(menuOpen === item.id ? null : item.id) }}>
                                    <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
                                        <circle cx="12" cy="5" r="1.5" /><circle cx="12" cy="12" r="1.5" /><circle cx="12" cy="19" r="1.5" />
                                    </svg>
                                    {menuOpen === item.id && (
                                        <div className="continue-card-dropdown">
                                            <button onClick={(e) => { e.stopPropagation(); handleMarkComplete(item) }}>
                                                Mark Complete
                                            </button>
                                            <button onClick={(e) => { e.stopPropagation(); handleResetProgress(item) }}>
                                                Reset Progress
                                            </button>
                                            <button onClick={(e) => { e.stopPropagation(); setMenuOpen(null); navigate(`/book/${item.media_type}/${item.mediaId}`) }}>
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
