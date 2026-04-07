import React, { useState, useEffect, useRef, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { getPairs, startTranscription, getTranscriptionStatus, cancelTranscription, addToQueue } from '../api'

function TranscriptionPage({ tab }) {
    const [pairs, setPairs] = useState([])
    const [loading, setLoading] = useState(true)
    const [searchQuery, setSearchQuery] = useState('')
    const [error, setError] = useState('')
    const [statuses, setStatuses] = useState({})
    const [viewMode, setViewMode] = useState('series') // 'series' | 'flat'
    const [expandedSeries, setExpandedSeries] = useState(new Set())
    const pollingRef = useRef({})

    const activeTab = tab || 'not-transcribed'

    const loadData = async () => {
        try {
            const p = await getPairs()
            setPairs(p)

            // Load statuses for actively transcribing pairs only
            const statusPromises = p
                .filter(pair => pair.status === 'transcribing')
                .map(async pair => {
                    try {
                        const status = await getTranscriptionStatus(pair.id)
                        return [pair.id, status]
                    } catch {
                        return [pair.id, null]
                    }
                })

            const results = await Promise.all(statusPromises)
            const statusMap = {}
            results.forEach(([id, status]) => { if (status) statusMap[id] = status })
            setStatuses(statusMap)

            // Resume polling for existing transcriptions
            p.filter(pair => pair.status === 'transcribing').forEach(pair => {
                if (!pollingRef.current[pair.id]) {
                    startPolling(pair.id)
                }
            })

        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => {
        loadData()
        return () => {
            Object.values(pollingRef.current).forEach(clearInterval)
        }
    }, [])

    const startPolling = (pairId) => {
        if (pollingRef.current[pairId]) return

        pollingRef.current[pairId] = setInterval(async () => {
            try {
                const s = await getTranscriptionStatus(pairId)
                setStatuses(prev => ({ ...prev, [pairId]: s }))

                if (s.status === 'synced' || s.status === 'error') {
                    stopPolling(pairId)
                    const p = await getPairs()
                    setPairs(p)
                }
            } catch (err) {
                console.error('Polling error:', err)
            }
        }, 3000)
    }

    const stopPolling = (pairId) => {
        if (pollingRef.current[pairId]) {
            clearInterval(pollingRef.current[pairId])
            delete pollingRef.current[pairId]
        }
    }

    const handleStart = async (pairId) => {
        setError('')
        try {
            await startTranscription(pairId)
            setPairs(prev => prev.map(p =>
                p.id === pairId ? { ...p, status: 'transcribing' } : p
            ))
            startPolling(pairId)
        } catch (err) {
            setError(err.message)
        }
    }

    const handleAddAllToQueue = async () => {
        if (notTranscribedPairs.length === 0) return
        setError('')
        try {
            const ids = notTranscribedPairs.map(p => p.id)
            await addToQueue(ids)
            loadData()
        } catch (err) {
            setError(err.message)
        }
    }

    const handleQueueSeries = async (seriesPairs) => {
        setError('')
        try {
            const ids = seriesPairs.map(p => p.id)
            await addToQueue(ids)
            // Update local state immediately (same as handleStart does for individual pairs)
            setPairs(prev => prev.map(p =>
                ids.includes(p.id) ? { ...p, status: 'transcribing' } : p
            ))
            // Start polling for each queued pair
            ids.forEach(id => startPolling(id))
        } catch (err) {
            setError(err.message)
        }
    }

    const handleCancel = async (pairId) => {
        if (!confirm("Are you sure you want to cancel this transcription?")) return

        try {
            await cancelTranscription(pairId)
            stopPolling(pairId)

            setStatuses(prev => ({
                ...prev,
                [pairId]: {
                    status: 'error',
                    message: 'Cancelled by user',
                    progress: null
                }
            }))

            loadData()

        } catch (err) {
            setError(err.message)
        }
    }

    const toggleSeriesExpanded = (seriesName) => {
        setExpandedSeries(prev => {
            const next = new Set(prev)
            if (next.has(seriesName)) {
                next.delete(seriesName)
            } else {
                next.add(seriesName)
            }
            return next
        })
    }

    const getStatusDisplay = (pair) => {
        const localStatus = statuses[pair.id]
        const currentStatus = localStatus?.status || pair.status
        const message = localStatus?.message || (currentStatus === 'synced' ? 'Sync complete' : null)
        const progress = localStatus?.progress

        if (currentStatus !== 'transcribing' && !localStatus) return null

        return (
            <div style={{ marginTop: '12px' }}>
                {message && (
                    <div style={{
                        fontSize: '0.85rem',
                        color: currentStatus === 'error' ? 'var(--error)' : 'var(--text-secondary)',
                        fontStyle: currentStatus === 'error' ? 'italic' : 'normal',
                        fontWeight: currentStatus === 'transcribing' ? 500 : 400,
                        marginBottom: progress != null ? '8px' : '0'
                    }}>
                        {currentStatus === 'error' ? '❌ ' : ''}{message}
                    </div>
                )}
                {progress != null && currentStatus === 'transcribing' && (
                    <div>
                        <div className="progress-bar">
                            <div
                                className="progress-fill"
                                style={{ width: `${(progress * 100).toFixed(1)}%`, transition: 'width 0.5s ease' }}
                            ></div>
                        </div>
                        <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '4px' }}>
                            {(progress * 100).toFixed(1)}% overall
                        </div>
                    </div>
                )}
            </div>
        )
    }

    const canTranscribe = (pair) => {
        return ['auto_matched', 'manual_matched', 'error'].includes(pair.status)
    }

    const matchedPairs = useMemo(() =>
        pairs.filter(p => p.status !== 'unmatched'),
    [pairs])

    // Search includes series name in addition to title and author
    const filteredPairs = useMemo(() => matchedPairs.filter(p => {
        if (!searchQuery) return true
        const q = searchQuery.toLowerCase()
        const series = (p.ebook?.series || p.audiobook?.series || '').toLowerCase()
        return (
            (p.ebook?.title || '').toLowerCase().includes(q) ||
            (p.audiobook?.title || '').toLowerCase().includes(q) ||
            (p.ebook?.author || '').toLowerCase().includes(q) ||
            series.includes(q)
        )
    }), [matchedPairs, searchQuery])

    const notTranscribedPairs = useMemo(() =>
        filteredPairs.filter(p => ['auto_matched', 'manual_matched', 'error'].includes(p.status)),
    [filteredPairs])

    const inProgress = useMemo(() =>
        filteredPairs.filter(p => p.status === 'transcribing'),
    [filteredPairs])

    const transcribed = useMemo(() =>
        filteredPairs.filter(p => p.status === 'synced'),
    [filteredPairs])

    // Series grouping for the Not Transcribed tab (must be before early return)
    const { seriesGroups, noSeriesPairs } = useMemo(() => {
        const groups = {}
        const noSeries = []

        notTranscribedPairs.forEach(pair => {
            const series = pair.ebook?.series || pair.audiobook?.series
            const seriesIndex = pair.ebook?.series_index ?? pair.audiobook?.series_index
            if (series) {
                if (!groups[series]) groups[series] = { name: series, pairs: [] }
                groups[series].pairs.push({ ...pair, _seriesIndex: seriesIndex })
            } else {
                noSeries.push(pair)
            }
        })

        Object.values(groups).forEach(g => {
            g.pairs.sort((a, b) => (a._seriesIndex ?? 999) - (b._seriesIndex ?? 999))
        })

        const sortedGroups = Object.values(groups).sort((a, b) =>
            a.name.localeCompare(b.name)
        )

        return { seriesGroups: sortedGroups, noSeriesPairs: noSeries }
    }, [notTranscribedPairs])

    if (loading) {
        return <div className="loading-page"><div className="spinner"></div> Loading...</div>
    }

    const renderPairCard = (pair, showActions = false) => (
        <div key={pair.id} className="card">
            <div style={{ fontWeight: 600, fontSize: '1.05rem', marginBottom: '8px' }}>
                <Link to={`/book/ebook/${pair.ebook.id}`} style={{ color: 'inherit', textDecoration: 'none' }}>
                    {pair.ebook.title}
                </Link>
            </div>
            <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '12px' }}>
                📚 {pair.ebook.format} · 🎧 {pair.audiobook.format}
                {pair.ebook.author && ` · ${pair.ebook.author}`}
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '8px' }}>
                <span className={`badge badge-${pair.status}`}>
                    {pair.status === 'synced' ? '✅ Synced' :
                        pair.status === 'transcribing' ? '⏳ Transcribing' :
                            pair.status === 'error' ? '❌ Error' :
                                '⏸️ Ready'}
                </span>

                {showActions && canTranscribe(pair) && (
                    <button
                        className="btn btn-primary btn-sm"
                        onClick={() => handleStart(pair.id)}
                    >
                        📋 {pair.status === 'error' ? 'Retry → Queue' : 'Add to Queue'}
                    </button>
                )}

                {pair.status === 'synced' && (
                    <Link
                        to={`/transcription/edit/${pair.id}`}
                        className="btn btn-secondary btn-sm"
                        style={{ textDecoration: 'none' }}
                    >
                        ✏️ Edit
                    </Link>
                )}

                {pair.status === 'transcribing' && (
                    <button
                        className="btn btn-danger btn-sm"
                        onClick={() => handleCancel(pair.id)}
                        style={{ backgroundColor: '#ffebee', color: '#d32f2f', border: '1px solid #ffcdd2' }}
                    >
                        🛑 Cancel
                    </button>
                )}
            </div>

            {getStatusDisplay(pair)}

            {pair.synced_at && (
                <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '8px' }}>
                    Synced: {new Date(pair.synced_at).toLocaleString()}
                </div>
            )}
        </div>
    )

    const renderSeriesCard = (seriesName, seriesPairs) => {
        const isCollapsed = !expandedSeries.has(seriesName)
        const canQueueAny = seriesPairs.some(p => canTranscribe(p))

        return (
            <div key={seriesName} className="card" style={{ marginBottom: '16px' }}>
                {/* Series header */}
                <div style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '12px',
                    cursor: 'pointer',
                    userSelect: 'none',
                }}
                    onClick={() => toggleSeriesExpanded(seriesName)}
                >
                    <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: '10px' }}>
                        <span style={{ fontSize: '1.05rem', fontWeight: 600 }}>📖 {seriesName}</span>
                        <span style={{
                            fontSize: '0.78rem',
                            background: 'var(--border)',
                            borderRadius: '12px',
                            padding: '2px 8px',
                            color: 'var(--text-secondary)',
                        }}>
                            {seriesPairs.length} {seriesPairs.length === 1 ? 'book' : 'books'}
                        </span>
                    </div>
                    {canQueueAny && (
                        <button
                            className="btn btn-primary btn-sm"
                            onClick={(e) => { e.stopPropagation(); handleQueueSeries(seriesPairs.filter(p => canTranscribe(p))) }}
                        >
                            📋 Queue All {seriesPairs.filter(p => canTranscribe(p)).length}
                        </button>
                    )}
                    <span style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>
                        {isCollapsed ? '▶' : '▼'}
                    </span>
                </div>

                {/* Book rows */}
                {!isCollapsed && (
                    <div style={{ marginTop: '12px', borderTop: '1px solid var(--border)', paddingTop: '12px' }}>
                        {seriesPairs.map(pair => (
                            <div key={pair.id} style={{
                                display: 'flex',
                                alignItems: 'center',
                                gap: '12px',
                                padding: '8px 0',
                                borderBottom: '1px solid var(--border)',
                            }}>
                                {/* Series index */}
                                <div style={{
                                    width: '28px',
                                    textAlign: 'center',
                                    fontSize: '0.8rem',
                                    color: 'var(--text-muted)',
                                    flexShrink: 0,
                                }}>
                                    {pair._seriesIndex != null ? `#${pair._seriesIndex}` : '—'}
                                </div>

                                {/* Title + formats */}
                                <div style={{ flex: 1, minWidth: 0 }}>
                                    <div style={{ fontWeight: 500, fontSize: '0.95rem', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                        <Link to={`/book/ebook/${pair.ebook.id}`} style={{ color: 'inherit', textDecoration: 'none' }}>
                                            {pair.ebook.title}
                                        </Link>
                                    </div>
                                    <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: '2px' }}>
                                        📚 {pair.ebook.format} · 🎧 {pair.audiobook.format}
                                    </div>
                                </div>

                                {/* Status badge */}
                                <span className={`badge badge-${pair.status}`} style={{ flexShrink: 0 }}>
                                    {pair.status === 'error' ? '❌ Error' : '⏸️ Ready'}
                                </span>

                                {/* Action button */}
                                {canTranscribe(pair) && (
                                    <button
                                        className="btn btn-primary btn-sm"
                                        style={{ flexShrink: 0 }}
                                        onClick={() => handleStart(pair.id)}
                                    >
                                        📋 {pair.status === 'error' ? 'Retry' : 'Queue'}
                                    </button>
                                )}
                            </div>
                        ))}
                    </div>
                )}
            </div>
        )
    }

    return (
        <div>
            <div className="page-header">
                <h2>Transcription</h2>
                <p>Transcribe audiobooks and align them to their ebook text to enable sync.</p>
            </div>

            {error && <div className="alert alert-error">⚠️ {error}</div>}

            {/* Stats */}
            <div className="stat-grid">
                <div className="stat-card">
                    <div className="stat-icon yellow">⏸️</div>
                    <div>
                        <div className="stat-value">{notTranscribedPairs.length}</div>
                        <div className="stat-label">Not Transcribed</div>
                    </div>
                </div>
                <div className="stat-card">
                    <div className="stat-icon purple">⏳</div>
                    <div>
                        <div className="stat-value">{inProgress.length}</div>
                        <div className="stat-label">In Progress</div>
                    </div>
                </div>
                <div className="stat-card">
                    <div className="stat-icon green">✅</div>
                    <div>
                        <div className="stat-value">{transcribed.length}</div>
                        <div className="stat-label">Transcribed</div>
                    </div>
                </div>
            </div>

            <div style={{ marginBottom: '24px' }}>
                <input
                    type="text"
                    className="form-control"
                    placeholder="Search by title, author, or series..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    style={{ width: '100%', padding: '10px 14px', borderRadius: '8px', border: '1px solid var(--border)' }}
                />
            </div>

            {/* ====== Not Transcribed Tab ====== */}
            {activeTab === 'not-transcribed' && (
                <div>
                    {notTranscribedPairs.length === 0 ? (
                        <div className="card">
                            <div className="empty-state">
                                <div className="icon">🎙️</div>
                                <h3>No pairs waiting for transcription</h3>
                                <p>
                                    {matchedPairs.length === 0
                                        ? 'Match some ebooks with audiobooks on the Book Pairs page first.'
                                        : 'All matched pairs have been transcribed or are in progress.'}
                                </p>
                            </div>
                        </div>
                    ) : (
                        <>
                            {/* Controls row */}
                            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '16px', flexWrap: 'wrap', gap: '8px' }}>
                                {/* View toggle */}
                                <div style={{ display: 'flex', gap: '6px' }}>
                                    <button
                                        className={`btn btn-sm ${viewMode === 'series' ? 'btn-primary' : 'btn-secondary'}`}
                                        onClick={() => setViewMode('series')}
                                    >
                                        📚 By Series
                                    </button>
                                    <button
                                        className={`btn btn-sm ${viewMode === 'flat' ? 'btn-primary' : 'btn-secondary'}`}
                                        onClick={() => setViewMode('flat')}
                                    >
                                        ☰ All Books
                                    </button>
                                </div>

                                {/* Queue all */}
                                {notTranscribedPairs.length > 1 && (
                                    <button
                                        className="btn btn-primary btn-sm"
                                        onClick={handleAddAllToQueue}
                                    >
                                        📋 Queue All {notTranscribedPairs.length}
                                    </button>
                                )}
                            </div>

                            {/* Series view */}
                            {viewMode === 'series' && (
                                <div>
                                    {seriesGroups.map(group =>
                                        renderSeriesCard(group.name, group.pairs)
                                    )}
                                    {noSeriesPairs.length > 0 &&
                                        renderSeriesCard('No Series', noSeriesPairs)
                                    }
                                </div>
                            )}

                            {/* Flat view */}
                            {viewMode === 'flat' && (
                                <div className="card-grid">
                                    {notTranscribedPairs.map(pair => renderPairCard(pair, true))}
                                </div>
                            )}
                        </>
                    )}
                </div>
            )}

            {/* ====== In Progress Tab ====== */}
            {activeTab === 'in-progress' && (
                <div>
                    {inProgress.length === 0 ? (
                        <div className="card">
                            <div className="empty-state">
                                <div className="icon">⏳</div>
                                <h3>No transcriptions in progress</h3>
                                <p>Start a transcription from the "Not Transcribed" tab to see it here.</p>
                            </div>
                        </div>
                    ) : (
                        <div className="card-grid">
                            {inProgress.map(pair => renderPairCard(pair, false))}
                        </div>
                    )}
                </div>
            )}

            {/* ====== Transcribed Tab ====== */}
            {activeTab === 'transcribed' && (
                <div>
                    {transcribed.length === 0 ? (
                        <div className="card">
                            <div className="empty-state">
                                <div className="icon">✅</div>
                                <h3>No transcribed pairs yet</h3>
                                <p>Once transcription completes successfully, pairs will appear here.</p>
                            </div>
                        </div>
                    ) : (
                        <div className="card-grid">
                            {transcribed.map(pair => renderPairCard(pair, false))}
                        </div>
                    )}
                </div>
            )}
        </div>
    )
}

export default TranscriptionPage
