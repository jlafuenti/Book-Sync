import React, { useState, useEffect } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { getEbook, getAudiobook, updateEbookMetadata, updateAudiobookMetadata, rescanBook, getSettings, enrichAudiobookFromAbs, getProgress, updateProgress, resetPairProgress, getBookmark, updateBookmark, getDeviceId, getDeviceName } from '../api'
import ReactMarkdown from 'react-markdown'
import EnhancedMetadataModal from '../components/EnhancedMetadataModal'
import EbookReader from '../components/EbookReader'
import { AudioPlayerView } from '../components/AudioPlayer'
import CoverImg from '../components/CoverImg'
import { useAuth } from '../contexts/AuthContext'
import { useAudioPlayer } from '../contexts/AudioPlayerContext'

function formatBytes(bytes) {
    if (!bytes) return '—'
    if (bytes < 1024) return bytes + ' B'
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
    if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
    return (bytes / (1024 * 1024 * 1024)).toFixed(2) + ' GB'
}

function formatDuration(seconds) {
    if (!seconds) return '—'
    const h = Math.floor(seconds / 3600)
    const m = Math.floor((seconds % 3600) / 60)
    const s = seconds % 60
    if (h > 0) return `${h}h ${m}m ${s}s`
    if (m > 0) return `${m}m ${s}s`
    return `${s}s`
}

function formatDate(iso) {
    if (!iso) return '—'
    return new Date(iso).toLocaleDateString('en-US', {
        year: 'numeric', month: 'short', day: 'numeric',
        hour: '2-digit', minute: '2-digit'
    })
}

function BookDetailPage() {
    const { hasMinRole } = useAuth()
    const canEdit = hasMinRole('editor')
    const { type, id } = useParams()
    const navigate = useNavigate()
    const [book, setBook] = useState(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)
    const [showEditModal, setShowEditModal] = useState(false)
    const [editModalTab, setEditModalTab] = useState('Details')
    const [rescanning, setRescanning] = useState(false)
    const [absEnabled, setAbsEnabled] = useState(false)
    const [enriching, setEnriching] = useState(false)
    const [toast, setToast] = useState(null)
    const [progress, setProgress] = useState(null)
    const [readerOpen, setReaderOpen] = useState(false)
    const [readerInitialChapter, setReaderInitialChapter] = useState(null)
    const [playerOpen, setPlayerOpen] = useState(false)
    const audioPlayer = useAudioPlayer()

    useEffect(() => {
        setLoading(true)
        setError(null)
        const fetchBook = type === 'ebook' ? getEbook : getAudiobook
        fetchBook(id)
            .then(data => {
                setBook(data)
                setLoading(false)
            })
            .catch(err => {
                setError(err.message || 'Failed to load book')
                setLoading(false)
            })
        if (type === 'audiobook') {
            getSettings().then(s => setAbsEnabled(s.abs_enabled === true || s.abs_enabled === 'true')).catch(() => {})
        }
        // Fetch reading/listening progress
        getProgress(type, id).then(setProgress).catch(() => setProgress(null))
    }, [type, id])

    const handleSaveMetadata = async (bookId, data) => {
        const updateFunc = type === 'ebook' ? updateEbookMetadata : updateAudiobookMetadata;
        const updatedBook = await updateFunc(bookId, data);
        setBook(updatedBook);
    }

    const showToast = (message, type = 'success') => {
        setToast({ message, type })
        setTimeout(() => setToast(null), 4000)
    }

    const handleEnrichFromAbs = async () => {
        setEnriching(true)
        try {
            const result = await enrichAudiobookFromAbs(id)
            if (result.book) setBook(result.book)
            const type = result.status === 'enriched' ? 'success'
                       : result.status === 'tag_write_failed' ? 'error'
                       : result.status === 'no_match' ? 'warning' : 'info'
            showToast(result.message, type)
        } catch (err) {
            showToast(err.message || 'Failed to enrich from Audiobookshelf', 'error')
        } finally {
            setEnriching(false)
        }
    }

    const handleRescan = async () => {
        if (!window.confirm("Rescan this file? This will overwrite its metadata with any tags found inside the file.")) return;
        setRescanning(true);
        setError(null);
        try {
            const updatedBook = await rescanBook(type, id);
            setBook(updatedBook);
        } catch (err) {
            setError(err.message || 'Failed to rescan file');
        } finally {
            setRescanning(false);
        }
    }

    if (loading) {
        return (
            <div className="loading-page" style={{ minHeight: '60vh' }}>
                <div className="spinner"></div>
                <span>Loading book details...</span>
            </div>
        )
    }

    if (error) {
        return (
            <div className="empty-state">
                <div className="icon">⚠️</div>
                <h3>Error</h3>
                <p>{error}</p>
                <button className="btn btn-secondary" onClick={() => navigate(-1)}>← Go Back</button>
            </div>
        )
    }

    if (!book) return null

    const isAudiobook = type === 'audiobook'
    const initials = (book.title || '?')[0].toUpperCase()

    // Build metadata rows
    const metadataRows = [
        { label: 'Title', value: book.title },
        { label: 'Author', value: book.author },
        { label: 'Series', value: book.series ? `${book.series}${book.series_index != null ? ` #${book.series_index}` : ''}` : null },
        { label: 'Publisher', value: book.publisher },
        { label: 'Published', value: book.publish_year },
        { label: 'Language', value: book.language },
        { label: 'Narrators', value: book.narrators, show: isAudiobook },
        { label: 'ISBN', value: book.isbn },
        { label: 'ASIN', value: book.asin },
        { label: 'Genres', value: book.genres },
        { label: 'Tags', value: book.tags },
    ].filter(r => r.show !== false)

    const fileRows = [
        { label: 'Format', value: book.format?.toUpperCase() },
        { label: 'File Size', value: formatBytes(book.file_size) },
        { label: 'Duration', value: formatDuration(book.duration_seconds), show: isAudiobook },
        { label: 'Filename', value: book.filename },
        { label: 'File Path', value: book.file_path },
        { label: 'File Hash', value: book.file_hash || '—' },
        { label: 'Added', value: formatDate(book.uploaded_at) },
        { label: 'Metadata Source', value: book.metadata_source || '—' },
        { label: 'Metadata Pattern', value: book.metadata_pattern || '—' },
    ].filter(r => r.show !== false)

    const flagRows = [
        book.is_explicit != null && { label: 'Explicit', value: book.is_explicit ? 'Yes' : 'No' },
        book.is_abridged != null && { label: 'Abridged', value: book.is_abridged ? 'Yes' : 'No' },
    ].filter(Boolean)

    const toastColors = {
        success: 'var(--success)',
        error: 'var(--error)',
        warning: 'var(--warning)',
        info: 'var(--accent)',
    }

    return (
        <div className="book-detail-page">
            {/* Toast notification */}
            {toast && (
                <div style={{
                    position: 'fixed', bottom: '24px', right: '24px', zIndex: 1000,
                    background: 'var(--bg-card)', border: `1px solid ${toastColors[toast.type]}`,
                    borderLeft: `4px solid ${toastColors[toast.type]}`,
                    borderRadius: '8px', padding: '12px 16px', maxWidth: '360px',
                    boxShadow: '0 4px 12px rgba(0,0,0,0.3)', fontSize: '0.9rem',
                    color: 'var(--text-primary)',
                }}>
                    {toast.message}
                </div>
            )}
            {/* Back button */}
            <button
                className="btn btn-secondary btn-sm"
                onClick={() => navigate(-1)}
                style={{ marginBottom: 24 }}
            >
                ← Back
            </button>

            {/* Hero header */}
            <div className="book-detail-hero">
                <div className="book-detail-cover">
                    {book.cover_path ? (
                        <CoverImg path={book.cover_path} alt={book.title} />
                    ) : (
                        <div className="book-detail-cover-placeholder">
                            <span className="book-detail-cover-initial">{initials}</span>
                            <span className="book-detail-cover-type">{isAudiobook ? '🎧' : '📚'}</span>
                        </div>
                    )}
                </div>
                <div className="book-detail-hero-info">
                    <div className="book-detail-type-badge">
                        {isAudiobook ? '🎧 Audiobook' : '📚 Ebook'}
                    </div>
                    <h1 className="book-detail-title">{book.title}</h1>
                    {book.author && <p className="book-detail-author">by {book.author}</p>}
                    {book.series && (
                        <p className="book-detail-series">
                            {book.series}{book.series_index != null ? ` · Book ${book.series_index}` : ''}
                        </p>
                    )}
                    {book.description && (
                        <div className="book-detail-description">
                            <ReactMarkdown>{book.description}</ReactMarkdown>
                        </div>
                    )}
                    {/* Progress display */}
                    {progress && !progress.is_completed && (
                        <div className="book-detail-progress-section">
                            {isAudiobook && !book.duration_seconds ? (
                                <span className="book-detail-progress-label">
                                    {formatDuration(Math.floor((progress.audio_position_ms || 0) / 1000))} listened
                                </span>
                            ) : (
                                <>
                                    <div className="book-detail-progress-bar">
                                        <div className="book-detail-progress-fill" style={{
                                            width: `${isAudiobook
                                                ? ((progress.audio_position_ms / 1000) / book.duration_seconds) * 100
                                                : (progress.epub_progress_percent || 0)}%`
                                        }} />
                                    </div>
                                    <span className="book-detail-progress-label">
                                        {isAudiobook
                                            ? `${formatDuration(Math.floor((progress.audio_position_ms || 0) / 1000))} / ${formatDuration(book.duration_seconds)}`
                                            : `${Math.round(progress.epub_progress_percent || 0)}%`
                                        }
                                    </span>
                                </>
                            )}
                        </div>
                    )}
                    {progress?.is_completed && (
                        <div className="book-detail-progress-section" style={{ background: 'var(--success-bg)' }}>
                            <span className="book-detail-progress-label" style={{ color: 'var(--success)' }}>Completed</span>
                        </div>
                    )}

                    <div className="book-detail-actions">
                        {/* Read / Listen buttons */}
                        {!isAudiobook && book.format === 'epub' && (
                            <button className="btn btn-primary" onClick={() => setReaderOpen(true)}>
                                {progress && !progress.is_completed && progress.epub_progress_percent > 0
                                    ? `Continue Reading (${Math.round(progress.epub_progress_percent)}%)`
                                    : 'Read'}
                            </button>
                        )}
                        {isAudiobook && (
                            <button className="btn btn-primary" onClick={() => {
                                audioPlayer.play(Number(id), book, progress?.audio_position_ms || 0, book.paired_with?.id || null)
                                setPlayerOpen(true)
                            }}>
                                {progress && !progress.is_completed && progress.audio_position_ms > 0
                                    ? `Continue Listening (${formatDuration(Math.floor(progress.audio_position_ms / 1000))})`
                                    : 'Listen'}
                            </button>
                        )}
                        {/* Mark Complete / Reset Progress */}
                        {progress && !progress.is_completed && (
                            <button className="btn btn-secondary" onClick={async () => {
                                await updateProgress(type, id, {
                                    is_completed: true,
                                    device_id: getDeviceId(),
                                    device_name: getDeviceName(),
                                    captured_at: new Date().toISOString(),
                                })
                                setProgress(p => ({ ...p, is_completed: true }))
                                showToast('Marked as complete')
                            }}>
                                Mark Complete
                            </button>
                        )}
                        {progress && (progress.is_completed || progress.epub_progress_percent > 0 || progress.audio_position_ms > 0) && (
                            <button className="btn btn-secondary" onClick={async () => {
                                if (book.pair_id) {
                                    // Paired: the pair-scoped DELETE removes the
                                    // canonical bookmark + hints + user_progress
                                    // server-side. The legacy per-media zero-write
                                    // below left the bookmark in place, which
                                    // re-seeded progress right back (issue: reset
                                    // buttons not actually resetting).
                                    await resetPairProgress(book.pair_id)
                                } else {
                                    const resetData = {
                                        is_completed: false,
                                        device_id: getDeviceId(),
                                        device_name: getDeviceName(),
                                        captured_at: new Date().toISOString(),
                                    }
                                    if (!isAudiobook) { resetData.epub_progress_percent = 0; resetData.epub_cfi = ''; resetData.epub_chapter = 0 }
                                    else { resetData.audio_position_ms = 0 }
                                    await updateProgress(type, id, resetData)
                                }
                                setProgress(null)
                                showToast('Progress reset')
                            }}>
                                Reset Progress
                            </button>
                        )}

                        {canEdit && (
                            <button className="btn btn-primary" onClick={() => { setEditModalTab('Details'); setShowEditModal(true); }}>
                                ✏️ Edit Metadata
                            </button>
                        )}
                        {canEdit && isAudiobook && (
                            <button className="btn btn-secondary" onClick={() => { setEditModalTab('Chapters'); setShowEditModal(true); }}>
                                📑 Edit Chapters
                            </button>
                        )}
                        {canEdit && (
                            <button className="btn btn-secondary" onClick={() => { setEditModalTab('Match'); setShowEditModal(true); }}>
                                🔍 Match
                            </button>
                        )}
                        {canEdit && (
                            <button className="btn btn-secondary" onClick={handleRescan} disabled={rescanning}>
                                {rescanning ? '🔄 Rescanning...' : '🔄 Rescan File'}
                            </button>
                        )}
                        {canEdit && isAudiobook && absEnabled && (
                            <button className="btn btn-secondary" onClick={handleEnrichFromAbs} disabled={enriching}>
                                {enriching ? 'Enriching...' : '✨ Enrich from ABS'}
                            </button>
                        )}
                    </div>
                </div>
            </div>

            {/* Paired status */}
            {book.pair_id && (
                <div className="book-detail-section">
                    <h3 className="book-detail-section-title">🔗 Paired With</h3>
                    <div className="book-detail-paired-card">
                        <div className="book-detail-paired-icon">
                            {isAudiobook ? '📚' : '🎧'}
                        </div>
                        <div className="book-detail-paired-info">
                            <Link
                                to={`/book/${isAudiobook ? 'ebook' : 'audiobook'}/${book.paired_with?.id}`}
                                className="book-detail-paired-title"
                            >
                                {book.paired_with?.title || 'Unknown'}
                            </Link>
                            <span className="book-detail-paired-author">
                                {book.paired_with?.author || ''}
                            </span>
                        </div>
                        <span className={`badge badge-${book.pair_status}`}>
                            {book.pair_status?.replace('_', ' ')}
                        </span>
                    </div>
                </div>
            )}

            {/* Metadata section */}
            <div className="book-detail-section">
                <h3 className="book-detail-section-title">📋 Metadata</h3>
                <div className="book-detail-metadata-grid">
                    {metadataRows.map(row => (
                        <div className="book-detail-meta-row" key={row.label}>
                            <span className="book-detail-meta-label">{row.label}</span>
                            <span className="book-detail-meta-value">{row.value || '—'}</span>
                        </div>
                    ))}
                    {flagRows.map(row => (
                        <div className="book-detail-meta-row" key={row.label}>
                            <span className="book-detail-meta-label">{row.label}</span>
                            <span className="book-detail-meta-value">{row.value}</span>
                        </div>
                    ))}
                </div>
            </div>

            {/* File info section */}
            <div className="book-detail-section">
                <h3 className="book-detail-section-title">📁 File Information</h3>
                <div className="book-detail-metadata-grid">
                    {fileRows.map(row => (
                        <div className="book-detail-meta-row" key={row.label}>
                            <span className="book-detail-meta-label">{row.label}</span>
                            <span className="book-detail-meta-value book-detail-meta-mono">
                                {row.value || '—'}
                            </span>
                        </div>
                    ))}
                </div>
            </div>

            {showEditModal && (
                <EnhancedMetadataModal
                    book={book}
                    type={type}
                    initialTab={editModalTab}
                    onClose={() => setShowEditModal(false)}
                    onSave={handleSaveMetadata}
                />
            )}

            {/* Ebook Reader Overlay */}
            {readerOpen && (
                <EbookReader
                    ebookId={Number(id)}
                    pairId={book.pair_id || null}
                    initialCfi={progress?.epub_cfi || null}
                    initialChapter={readerInitialChapter}
                    bookTitle={book.title}
                    onClose={() => {
                        setReaderOpen(false)
                        setReaderInitialChapter(null)
                        getProgress(type, id).then(setProgress).catch(() => {})
                    }}
                    onSwitchToAudio={book.pair_id && book.paired_with ? async () => {
                        const bm = await getBookmark(book.pair_id).catch(() => null)
                        let audioPositionMs = bm?.audio_position_ms || 0
                        if (!audioPositionMs) {
                            const prog = await getProgress('audiobook', book.paired_with.id).catch(() => null)
                            audioPositionMs = prog?.audio_position_ms || 0
                        }
                        setReaderOpen(false)
                        setReaderInitialChapter(null)
                        audioPlayer.play(book.paired_with.id, book.paired_with, audioPositionMs, Number(id))
                        setPlayerOpen(true)
                    } : null}
                />
            )}

            {/* Audiobook Player Overlay */}
            {playerOpen && audioPlayer.currentAudiobook && (
                <AudioPlayerView
                    onClose={() => setPlayerOpen(false)}
                    onSwitchToEbook={book.pair_id && book.paired_with ? async (pairId, ebookId) => {
                        audioPlayer.pause()
                        const posMs = Math.floor(audioPlayer.currentTime * 1000)
                        await updateBookmark(pairId, { source: 'audiobook', audio_position_ms: posMs }).catch(() => {})
                        const bm = await getBookmark(pairId).catch(() => null)
                        setPlayerOpen(false)
                        setReaderInitialChapter(bm?.epub_chapter ?? null)
                        setReaderOpen(true)
                    } : null}
                />
            )}
        </div>
    )
}

export default BookDetailPage
