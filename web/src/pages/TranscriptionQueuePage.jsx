import React, { useState, useEffect, useRef } from 'react'
import {
    getTranscriptionQueue, getQueueHistory, removeFromQueue, cancelTranscription,
    updateQueuePriority, runQueueItemNow, getOffHoursStatus,
} from '../api'
import { useAuth } from '../contexts/AuthContext'

function TranscriptionQueuePage() {
    const { hasMinRole } = useAuth()
    const canManageQueue = hasMinRole('admin')
    // Cancel sits one rung lower than the rest of the queue controls: #207 gave
    // it an editor floor server-side, so rendering it to a plain user shows a
    // button whose only outcome is a 403 (issue #312).
    const canCancel = hasMinRole('editor')
    const [offHours, setOffHours] = useState(null)
    const [queue, setQueue] = useState([])
    const [history, setHistory] = useState([])
    const [loading, setLoading] = useState(true)
    const [historyLoading, setHistoryLoading] = useState(false)
    const [searchQuery, setSearchQuery] = useState('')
    const [error, setError] = useState('')
    const [activeTab, setActiveTab] = useState('queue')
    const [historySortCol, setHistorySortCol] = useState('started_at')
    const [historySortDir, setHistorySortDir] = useState('desc')
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

    // The off-hours window explains why a queue can be full and idle (#106).
    // Failing to read it must not blank the page — the queue itself still works.
    const loadOffHours = async () => {
        try {
            setOffHours(await getOffHoursStatus())
        } catch {
            setOffHours(null)
        }
    }

    const loadHistory = async () => {
        setHistoryLoading(true)
        try {
            const data = await getQueueHistory(100)
            setHistory(data)
        } catch (err) {
            setError(err.message)
        } finally {
            setHistoryLoading(false)
        }
    }

    useEffect(() => {
        loadQueue()
        loadOffHours()

        // Poll as long as there are active items
        pollingRef.current = setInterval(loadQueue, 3000)

        return () => {
            if (pollingRef.current) clearInterval(pollingRef.current)
        }
    }, [])

    useEffect(() => {
        if (activeTab === 'history') {
            loadHistory()
        }
    }, [activeTab])

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

    const handleRunNow = async (itemId) => {
        try {
            await runQueueItemNow(itemId)
            await Promise.all([loadQueue(), loadOffHours()])
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

    const formatDuration = (startStr, endStr) => {
        if (!startStr || !endStr) return '—'
        const start = new Date(startStr.endsWith('Z') ? startStr : `${startStr}Z`)
        const end = new Date(endStr.endsWith('Z') ? endStr : `${endStr}Z`)
        const diffMs = end - start
        if (diffMs < 0) return '—'
        const totalSec = Math.floor(diffMs / 1000)
        const hours = Math.floor(totalSec / 3600)
        const mins = Math.floor((totalSec % 3600) / 60)
        const secs = totalSec % 60
        if (hours > 0) return `${hours}h ${mins}m ${secs}s`
        if (mins > 0) return `${mins}m ${secs}s`
        return `${secs}s`
    }

    const formatDate = (dateStr) => {
        if (!dateStr) return '—'
        // Queue timestamps are naive UTC (the DB columns are tz-less), so they
        // need a Z. The off-hours window's opens_at/closes_at already carry an
        // offset — stamping a Z on those would produce an Invalid Date.
        const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(dateStr)
        return new Date(hasZone ? dateStr : `${dateStr}Z`).toLocaleString()
    }

    const getStatusBadge = (status) => {
        const styles = {
            completed: { backgroundColor: '#e8f5e9', color: '#2e7d32', border: '1px solid #c8e6c9' },
            failed: { backgroundColor: '#ffebee', color: '#c62828', border: '1px solid #ffcdd2' },
            cancelled: { backgroundColor: '#fff3e0', color: '#e65100', border: '1px solid #ffe0b2' },
            in_progress: { backgroundColor: '#e3f2fd', color: '#1565c0', border: '1px solid #bbdefb' },
            pending: { backgroundColor: '#f3e5f5', color: '#7b1fa2', border: '1px solid #e1bee7' },
        }
        const labels = {
            completed: '✅ Completed',
            failed: '❌ Failed',
            cancelled: '⚪ Cancelled',
            in_progress: '🔄 In Progress',
            pending: '⏳ Pending',
        }
        return (
            <span style={{
                ...styles[status] || {},
                padding: '3px 10px',
                borderRadius: '12px',
                fontSize: '0.75rem',
                fontWeight: 600,
                whiteSpace: 'nowrap',
            }}>
                {labels[status] || status}
            </span>
        )
    }

    if (loading) {
        return <div className="loading-page"><div className="spinner"></div> Loading queue...</div>
    }

    const filteredQueue = queue.filter(q => {
        if (!searchQuery) return true
        const title = q.book_title || `Pair #${q.book_pair_id}`
        return title.toLowerCase().includes(searchQuery.toLowerCase())
    })

    const filteredHistory = history.filter(q => {
        if (!searchQuery) return true
        const title = q.book_title || `Pair #${q.book_pair_id}`
        return title.toLowerCase().includes(searchQuery.toLowerCase())
    })

    const sortedHistory = [...filteredHistory].sort((a, b) => {
        const aVal = a[historySortCol] ? new Date(a[historySortCol]) : null
        const bVal = b[historySortCol] ? new Date(b[historySortCol]) : null
        if (aVal === null && bVal === null) return 0
        if (aVal === null) return 1
        if (bVal === null) return -1
        return historySortDir === 'asc' ? aVal - bVal : bVal - aVal
    })

    const toggleHistorySort = (col) => {
        if (historySortCol === col) {
            setHistorySortDir(d => d === 'asc' ? 'desc' : 'asc')
        } else {
            setHistorySortCol(col)
            setHistorySortDir('asc')
        }
    }

    const SortIndicator = ({ col }) => {
        if (historySortCol !== col) return <span style={{ opacity: 0.3, marginLeft: 4 }}>⇅</span>
        return <span style={{ marginLeft: 4 }}>{historySortDir === 'asc' ? '↑' : '↓'}</span>
    }

    const activeItem = filteredQueue.find(q => q.status === 'in_progress')
    const pendingItems = filteredQueue.filter(q => q.status === 'pending')

    return (
        <div>
            <div className="page-header">
                <h2>📋 Transcription Queue</h2>
                <p>Monitor and manage transcription jobs. The queue processes one book at a time.</p>
            </div>

            {error && <div className="alert alert-error">⚠️ {error}</div>}

            {/* Off-hours window banner (#106) */}
            {offHours?.enabled && (
                <div className={`alert ${offHours.open ? 'alert-success' : 'alert-info'}`}>
                    {offHours.open
                        ? <>🌙 Off-hours scheduling is on — the queue is running now and closes
                            at {offHours.end} {offHours.timezone}.</>
                        : <>🌙 Off-hours scheduling is on — queued books wait until {offHours.start} {offHours.timezone}
                            {offHours.opens_at ? ` (${formatDate(offHours.opens_at)})` : ''}. Use
                            "Run now" to start one immediately.</>}
                </div>
            )}

            {/* Tab Buttons */}
            <div style={{ display: 'flex', gap: '8px', marginBottom: '20px' }}>
                <button
                    className={`btn ${activeTab === 'queue' ? 'btn-primary' : 'btn-secondary'}`}
                    onClick={() => setActiveTab('queue')}
                    style={{ padding: '8px 20px', fontWeight: activeTab === 'queue' ? 700 : 500 }}
                >
                    🔄 Active Queue
                </button>
                <button
                    className={`btn ${activeTab === 'history' ? 'btn-primary' : 'btn-secondary'}`}
                    onClick={() => setActiveTab('history')}
                    style={{ padding: '8px 20px', fontWeight: activeTab === 'history' ? 700 : 500 }}
                >
                    📜 History
                </button>
            </div>

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
                    placeholder={activeTab === 'queue' ? "Search queue by book title..." : "Search history by book title..."}
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    style={{ width: '100%', padding: '10px 14px', borderRadius: '8px', border: '1px solid var(--border)' }}
                />
            </div>

            {/* ============ ACTIVE QUEUE TAB ============ */}
            {activeTab === 'queue' && (
                <>
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
                                    {canCancel && (
                                    <button
                                        className="btn btn-danger btn-sm"
                                        onClick={() => handleCancel(activeItem)}
                                        style={{ backgroundColor: '#ffebee', color: '#d32f2f', border: '1px solid #ffcdd2', whiteSpace: 'nowrap' }}
                                    >
                                        🛑 Cancel
                                    </button>
                                    )}
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
                                        Started: {formatDate(activeItem.started_at)}
                                        {activeItem.retry_count > 0 && (
                                            <span style={{ marginLeft: '12px', color: '#e65100' }}>
                                                🔁 Retry #{activeItem.retry_count}
                                            </span>
                                        )}
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
                                                        {item.paused_at && (
                                                            <span style={{
                                                                marginLeft: '8px', fontSize: '0.7rem', fontWeight: 700,
                                                                padding: '2px 8px', borderRadius: '10px',
                                                                backgroundColor: '#e3f2fd', color: '#1565c0',
                                                                border: '1px solid #bbdefb',
                                                            }}>
                                                                ⏸ Paused — {((item.progress || 0) * 100).toFixed(0)}% done
                                                            </span>
                                                        )}
                                                        {item.force_run && !item.paused_at && (
                                                            <span style={{
                                                                marginLeft: '8px', fontSize: '0.7rem', fontWeight: 700,
                                                                padding: '2px 8px', borderRadius: '10px',
                                                                backgroundColor: '#fff3e0', color: '#e65100',
                                                                border: '1px solid #ffe0b2',
                                                            }}>
                                                                ⚡ Running next
                                                            </span>
                                                        )}
                                                    </div>
                                                    <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                                                        Priority: {item.priority} · Added: {formatDate(item.created_at)}
                                                        {item.retry_count > 0 && (
                                                            <span style={{ marginLeft: '8px', color: '#e65100' }}>
                                                                🔁 Retry #{item.retry_count}
                                                            </span>
                                                        )}
                                                    </div>
                                                    {item.message && (
                                                        <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginTop: '2px' }}>
                                                            {item.message}
                                                        </div>
                                                    )}
                                                </div>
                                            </div>

                                            {canManageQueue && (
                                                <div style={{ display: 'flex', gap: '4px' }}>
                                                    {offHours?.enabled && !offHours.open && !item.force_run && (
                                                        <button
                                                            className="btn btn-secondary btn-sm"
                                                            onClick={() => handleRunNow(item.id)}
                                                            title="Start this book now, ignoring the off-hours window"
                                                            style={{ padding: '4px 10px', fontSize: '0.8rem', whiteSpace: 'nowrap' }}
                                                        >
                                                            ⚡ Run now
                                                        </button>
                                                    )}
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
                                            )}
                                        </div>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                </>
            )}

            {/* ============ HISTORY TAB ============ */}
            {activeTab === 'history' && (
                <div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                        <h3 style={{ fontSize: '1rem', color: 'var(--text-secondary)', margin: 0 }}>
                            📜 Queue History ({filteredHistory.length} items)
                        </h3>
                        <button
                            className="btn btn-secondary btn-sm"
                            onClick={loadHistory}
                            disabled={historyLoading}
                            style={{ padding: '6px 14px', fontSize: '0.8rem' }}
                        >
                            {historyLoading ? '⏳ Loading...' : '🔄 Refresh'}
                        </button>
                    </div>

                    {historyLoading && history.length === 0 ? (
                        <div className="card">
                            <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-secondary)' }}>
                                <div className="spinner" style={{ marginBottom: '12px' }}></div>
                                Loading history...
                            </div>
                        </div>
                    ) : filteredHistory.length === 0 ? (
                        <div className="card">
                            <div className="empty-state">
                                <div className="icon">📜</div>
                                <h3>No history yet</h3>
                                <p>Completed, failed, and cancelled transcription jobs will appear here.</p>
                            </div>
                        </div>
                    ) : (
                        <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
                            <div style={{ overflowX: 'auto' }}>
                                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
                                    <thead>
                                        <tr style={{ backgroundColor: 'var(--bg-secondary)', borderBottom: '2px solid var(--border)' }}>
                                            <th style={thStyle}>Book</th>
                                            <th style={thStyle}>Status</th>
                                            <th style={thStyle}>Retries</th>
                                            <th style={{ ...thStyle, cursor: 'pointer', userSelect: 'none' }} onClick={() => toggleHistorySort('created_at')}>Created<SortIndicator col="created_at" /></th>
                                            <th style={{ ...thStyle, cursor: 'pointer', userSelect: 'none' }} onClick={() => toggleHistorySort('started_at')}>Started<SortIndicator col="started_at" /></th>
                                            <th style={{ ...thStyle, cursor: 'pointer', userSelect: 'none' }} onClick={() => toggleHistorySort('completed_at')}>Completed<SortIndicator col="completed_at" /></th>
                                            <th style={thStyle}>Duration</th>
                                            <th style={thStyle}>Message</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {sortedHistory.map(item => (
                                            <tr key={item.id} style={{ borderBottom: '1px solid var(--border)' }}>
                                                <td style={tdStyle}>
                                                    <div style={{ fontWeight: 600, maxWidth: '200px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                                        {item.book_title || `Pair #${item.book_pair_id}`}
                                                    </div>
                                                </td>
                                                <td style={tdStyle}>{getStatusBadge(item.status)}</td>
                                                <td style={{ ...tdStyle, textAlign: 'center' }}>
                                                    {item.retry_count > 0 ? (
                                                        <span style={{ color: '#e65100', fontWeight: 600 }}>
                                                            {item.retry_count}
                                                        </span>
                                                    ) : (
                                                        <span style={{ color: 'var(--text-muted)' }}>0</span>
                                                    )}
                                                </td>
                                                <td style={tdStyle}>
                                                    <span style={{ fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
                                                        {formatDate(item.created_at)}
                                                    </span>
                                                </td>
                                                <td style={tdStyle}>
                                                    <span style={{ fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
                                                        {formatDate(item.started_at)}
                                                    </span>
                                                </td>
                                                <td style={tdStyle}>
                                                    <span style={{ fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
                                                        {formatDate(item.completed_at)}
                                                    </span>
                                                </td>
                                                <td style={tdStyle}>
                                                    <span style={{ fontSize: '0.78rem', fontWeight: 500 }}>
                                                        {formatDuration(item.started_at, item.completed_at)}
                                                    </span>
                                                </td>
                                                <td style={tdStyle}>
                                                    <div style={{
                                                        maxWidth: '250px',
                                                        overflow: 'hidden',
                                                        textOverflow: 'ellipsis',
                                                        whiteSpace: 'nowrap',
                                                        fontSize: '0.78rem',
                                                        color: item.status === 'failed' ? '#c62828' : 'var(--text-secondary)',
                                                    }}
                                                        title={item.error_message || item.message || ''}
                                                    >
                                                        {item.error_message || item.message || '—'}
                                                    </div>
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    )
}

const thStyle = {
    padding: '10px 12px',
    textAlign: 'left',
    fontWeight: 600,
    fontSize: '0.78rem',
    color: 'var(--text-secondary)',
    whiteSpace: 'nowrap',
}

const tdStyle = {
    padding: '10px 12px',
    verticalAlign: 'middle',
}

export default TranscriptionQueuePage
