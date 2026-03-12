import React, { useState, useEffect, useRef } from 'react'
import { getTranscriptionQueue, removeFromQueue, cancelTranscription, updateQueuePriority } from '../api'

function TranscriptionQueuePage() {
    const [queue, setQueue] = useState([])
    const [loading, setLoading] = useState(true)
    const [searchQuery, setSearchQuery] = useState('')
    const [error, setError] = useState('')
    const pollingRef = useRef(null)

    const loadQueue = async () => {
        try {
            const data = await getTranscriptionQueue()
            setQueue(data)
        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => {
        loadQueue()

        // Poll as long as there are active items
        pollingRef.current = setInterval(loadQueue, 3000)

        return () => {
            if (pollingRef.current) clearInterval(pollingRef.current)
        }
    }, [])

    const handleRemove = async (itemId) => {
        if (!confirm('Remove this item from the queue?')) return
        try {
            await removeFromQueue(itemId)
            loadQueue()
        } catch (err) {
            setError(err.message)
        }
    }

    const handleCancel = async (item) => {
        if (!confirm('Cancel this transcription?')) return
        try {
            await cancelTranscription(item.book_pair_id)
            loadQueue()
        } catch (err) {
            setError(err.message)
        }
    }

    const handleMovePriority = async (itemId, direction) => {
        const item = queue.find(q => q.id === itemId)
        if (!item) return
        const newPriority = direction === 'up' ? Math.max(1, item.priority - 10) : item.priority + 10
        try {
            await updateQueuePriority(itemId, newPriority)
            loadQueue()
        } catch (err) {
            setError(err.message)
        }
    }

    if (loading) {
        return <div className="loading-page"><div className="spinner"></div> Loading queue...</div>
    }

    const filteredQueue = queue.filter(q => {
        if (!searchQuery) return true
        const title = q.book_title || `Pair #${q.book_pair_id}`
        return title.toLowerCase().includes(searchQuery.toLowerCase())
    })

    const activeItem = filteredQueue.find(q => q.status === 'in_progress')
    const pendingItems = filteredQueue.filter(q => q.status === 'pending')

    return (
        <div>
            <div className="page-header">
                <h2>📋 Transcription Queue</h2>
                <p>Monitor and manage transcription jobs. The queue processes one book at a time.</p>
            </div>

            {error && <div className="alert alert-error">⚠️ {error}</div>}

            {/* Stats */}
            <div className="stat-grid">
                <div className="stat-card">
                    <div className="stat-icon purple">⏳</div>
                    <div>
                        <div className="stat-value">{activeItem ? 1 : 0}</div>
                        <div className="stat-label">Active</div>
                    </div>
                </div>
                <div className="stat-card">
                    <div className="stat-icon yellow">📋</div>
                    <div>
                        <div className="stat-value">{pendingItems.length}</div>
                        <div className="stat-label">Pending</div>
                    </div>
                </div>
                <div className="stat-card">
                    <div className="stat-icon green">📊</div>
                    <div>
                        <div className="stat-value">{queue.length}</div>
                        <div className="stat-label">Total</div>
                    </div>
                </div>
            </div>

            <div style={{ marginBottom: '24px' }}>
                <input
                    type="text"
                    className="form-control"
                    placeholder="Search queue by book title..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    style={{ width: '100%', padding: '10px 14px', borderRadius: '8px', border: '1px solid var(--border)' }}
                />
            </div>

            {/* Active Job */}
            {activeItem && (
                <div style={{ marginBottom: '24px' }}>
                    <h3 style={{ marginBottom: '12px', fontSize: '1rem', color: 'var(--text-secondary)' }}>
                        🔄 Currently Processing
                    </h3>
                    <div className="card" style={{ borderLeft: '4px solid var(--primary)' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                            <div>
                                <div style={{ fontWeight: 600, fontSize: '1.05rem', marginBottom: '4px' }}>
                                    {activeItem.book_title || `Pair #${activeItem.book_pair_id}`}
                                </div>
                                <div style={{
                                    fontSize: '0.85rem',
                                    color: 'var(--text-secondary)',
                                    fontWeight: 500,
                                    marginBottom: '12px'
                                }}>
                                    {activeItem.message || 'Processing...'}
                                </div>
                            </div>
                            <button
                                className="btn btn-danger btn-sm"
                                onClick={() => handleCancel(activeItem)}
                                style={{ backgroundColor: '#ffebee', color: '#d32f2f', border: '1px solid #ffcdd2', whiteSpace: 'nowrap' }}
                            >
                                🛑 Cancel
                            </button>
                        </div>

                        {activeItem.progress != null && (
                            <div>
                                <div className="progress-bar">
                                    <div
                                        className="progress-fill"
                                        style={{ width: `${(activeItem.progress * 100).toFixed(1)}%`, transition: 'width 0.5s ease' }}
                                    ></div>
                                </div>
                                <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '4px' }}>
                                    {(activeItem.progress * 100).toFixed(1)}% complete
                                </div>
                            </div>
                        )}

                        {activeItem.started_at && (
                            <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '8px' }}>
                                Started: {new Date(activeItem.started_at).toLocaleString()}
                            </div>
                        )}
                    </div>
                </div>
            )}

            {/* Pending Queue */}
            <div>
                <h3 style={{ marginBottom: '12px', fontSize: '1rem', color: 'var(--text-secondary)' }}>
                    📋 Pending ({pendingItems.length})
                </h3>

                {pendingItems.length === 0 && !activeItem ? (
                    <div className="card">
                        <div className="empty-state">
                            <div className="icon">📋</div>
                            <h3>Queue is empty</h3>
                            <p>Add books to the queue from the "Not Transcribed" tab to start processing.</p>
                        </div>
                    </div>
                ) : pendingItems.length === 0 ? (
                    <div className="card">
                        <div style={{ textAlign: 'center', padding: '24px', color: 'var(--text-secondary)' }}>
                            No more items waiting. The current job is the last one!
                        </div>
                    </div>
                ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                        {pendingItems.map((item, idx) => (
                            <div key={item.id} className="card" style={{ padding: '12px 16px' }}>
                                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                                        <span style={{
                                            display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                                            width: '28px', height: '28px', borderRadius: '50%',
                                            backgroundColor: 'var(--bg-secondary)', color: 'var(--text-secondary)',
                                            fontSize: '0.8rem', fontWeight: 700
                                        }}>
                                            {idx + 1}
                                        </span>
                                        <div>
                                            <div style={{ fontWeight: 600 }}>
                                                {item.book_title || `Pair #${item.book_pair_id}`}
                                            </div>
                                            <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                                                Priority: {item.priority} · Added: {new Date(item.created_at).toLocaleString()}
                                            </div>
                                        </div>
                                    </div>

                                    <div style={{ display: 'flex', gap: '4px' }}>
                                        <button
                                            className="btn btn-secondary btn-sm"
                                            onClick={() => handleMovePriority(item.id, 'up')}
                                            title="Move up (higher priority)"
                                            disabled={idx === 0}
                                            style={{ padding: '4px 8px', fontSize: '0.8rem' }}
                                        >
                                            ⬆️
                                        </button>
                                        <button
                                            className="btn btn-secondary btn-sm"
                                            onClick={() => handleMovePriority(item.id, 'down')}
                                            title="Move down (lower priority)"
                                            disabled={idx === pendingItems.length - 1}
                                            style={{ padding: '4px 8px', fontSize: '0.8rem' }}
                                        >
                                            ⬇️
                                        </button>
                                        <button
                                            className="btn btn-danger btn-sm"
                                            onClick={() => handleRemove(item.id)}
                                            title="Remove from queue"
                                            style={{ padding: '4px 8px', fontSize: '0.8rem', backgroundColor: '#ffebee', color: '#d32f2f', border: '1px solid #ffcdd2' }}
                                        >
                                            ✕
                                        </button>
                                    </div>
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </div>
    )
}

export default TranscriptionQueuePage
