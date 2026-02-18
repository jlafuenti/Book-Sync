import React, { useState, useEffect, useRef } from 'react'
import { getPairs, startTranscription, getTranscriptionStatus, cancelTranscription } from '../api'

function TranscriptionPage() {
    const [pairs, setPairs] = useState([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState('')
    const [statuses, setStatuses] = useState({})
    const pollingRef = useRef({})

    const loadData = async () => {
        try {
            const p = await getPairs()
            setPairs(p)

            // Load statuses for transcribing pairs
            const statusPromises = p
                .filter(pair => ['transcribing', 'synced', 'error'].includes(pair.status))
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
            // Clean up polling intervals
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
                    // Refresh main list to update status badges
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
            const status = await startTranscription(pairId)
            setStatuses(prev => ({ ...prev, [pairId]: status }))

            // Optimistically update local pair status
            setPairs(prev => prev.map(p =>
                p.id === pairId ? { ...p, status: 'transcribing' } : p
            ))

            startPolling(pairId)
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

            // Refresh list to sync state
            loadData()

        } catch (err) {
            setError(err.message)
        }
    }

    const getStatusDisplay = (pair) => {
        const localStatus = statuses[pair.id]

        // If we have a local status update, use it over the prop
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

    if (loading) {
        return <div className="loading-page"><div className="spinner"></div> Loading...</div>
    }

    // Only show matched pairs (not unmatched ones)
    const matchedPairs = pairs.filter(p => p.status !== 'unmatched')

    return (
        <div>
            <div className="page-header">
                <h2>Transcription</h2>
                <p>Transcribe audiobooks and align them to their ebook text to enable sync.</p>
            </div>

            {error && <div className="alert alert-error">⚠️ {error}</div>}

            {matchedPairs.length === 0 ? (
                <div className="card">
                    <div className="empty-state">
                        <div className="icon">🎙️</div>
                        <h3>No matched pairs</h3>
                        <p>
                            Match some ebooks with audiobooks on the Book Pairs page first,
                            then come here to start transcription.
                        </p>
                    </div>
                </div>
            ) : (
                <div className="card-grid">
                    {matchedPairs.map(pair => (
                        <div key={pair.id} className="card">
                            <div style={{ fontWeight: 600, fontSize: '1.05rem', marginBottom: '8px' }}>
                                {pair.ebook.title}
                            </div>
                            <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '12px' }}>
                                📚 {pair.ebook.format} · 🎧 {pair.audiobook.format}
                            </div>

                            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '8px' }}>
                                <span className={`badge badge-${pair.status}`}>
                                    {pair.status === 'synced' ? '✅ Synced' :
                                        pair.status === 'transcribing' ? '⏳ Transcribing' :
                                            pair.status === 'error' ? '❌ Error' :
                                                '⏸️ Ready'}
                                </span>

                                {canTranscribe(pair) && (
                                    <button
                                        className="btn btn-primary btn-sm"
                                        onClick={() => handleStart(pair.id)}
                                    >
                                        🎙️ {pair.status === 'error' ? 'Retry' : 'Start'} Transcription
                                    </button>
                                )}

                                {pair.status === 'transcribing' && (
                                    <button
                                        className="btn btn-danger btn-sm"
                                        onClick={() => handleCancel(pair.id)}
                                        style={{ backgroundColor: '#ffebeel', color: '#d32f2f', border: '1px solid #ffcdd2' }}
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
                    ))}
                </div>
            )}
        </div>
    )
}

export default TranscriptionPage
