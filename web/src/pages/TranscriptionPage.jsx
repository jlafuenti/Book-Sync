import React, { useState, useEffect, useRef } from 'react'
import { getPairs, startTranscription, getTranscriptionStatus } from '../api'

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

    const handleStart = async (pairId) => {
        setError('')
        try {
            const status = await startTranscription(pairId)
            setStatuses(prev => ({ ...prev, [pairId]: status }))

            // Start polling for progress
            pollingRef.current[pairId] = setInterval(async () => {
                try {
                    const s = await getTranscriptionStatus(pairId)
                    setStatuses(prev => ({ ...prev, [pairId]: s }))
                    if (s.status === 'synced' || s.status === 'error') {
                        clearInterval(pollingRef.current[pairId])
                        delete pollingRef.current[pairId]
                        loadData()  // Reload to update pair status
                    }
                } catch (err) {
                    console.error('Polling error:', err)
                }
            }, 3000)
        } catch (err) {
            setError(err.message)
        }
    }

    const getStatusDisplay = (pair) => {
        const status = statuses[pair.id]
        if (!status) return null

        return (
            <div style={{ marginTop: '12px' }}>
                {status.progress != null && (
                    <div style={{ marginBottom: '8px' }}>
                        <div className="progress-bar">
                            <div
                                className="progress-fill"
                                style={{ width: `${(status.progress * 100).toFixed(0)}%` }}
                            ></div>
                        </div>
                        <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '4px' }}>
                            {(status.progress * 100).toFixed(0)}%
                        </div>
                    </div>
                )}
                {status.message && (
                    <div style={{
                        fontSize: '0.8rem',
                        color: status.status === 'error' ? 'var(--error)' : 'var(--text-secondary)',
                        fontStyle: 'italic'
                    }}>
                        {status.message}
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
