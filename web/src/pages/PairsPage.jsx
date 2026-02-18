import React, { useState, useEffect } from 'react'
import { getPairs, getEbooks, getAudiobooks, createPair, deletePair } from '../api'

function PairsPage() {
    const [pairs, setPairs] = useState([])
    const [ebooks, setEbooks] = useState([])
    const [audiobooks, setAudiobooks] = useState([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState('')
    const [showModal, setShowModal] = useState(false)
    const [selectedEbook, setSelectedEbook] = useState('')
    const [selectedAudiobook, setSelectedAudiobook] = useState('')

    const loadData = async () => {
        try {
            const [p, e, a] = await Promise.all([getPairs(), getEbooks(), getAudiobooks()])
            setPairs(p)
            setEbooks(e)
            setAudiobooks(a)
        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => { loadData() }, [])

    const handleCreatePair = async () => {
        if (!selectedEbook || !selectedAudiobook) return
        setError('')
        try {
            await createPair(parseInt(selectedEbook), parseInt(selectedAudiobook))
            setShowModal(false)
            setSelectedEbook('')
            setSelectedAudiobook('')
            await loadData()
        } catch (err) {
            setError(err.message)
        }
    }

    const handleDeletePair = async (pairId) => {
        if (!confirm('Delete this book pair? This will also remove its sync map.')) return
        try {
            await deletePair(pairId)
            await loadData()
        } catch (err) {
            setError(err.message)
        }
    }

    const statusBadge = (status) => {
        const labels = {
            unmatched: 'Unmatched',
            auto_matched: 'Auto Matched',
            manual_matched: 'Manual Match',
            transcribing: 'Transcribing',
            synced: 'Synced ✓',
            error: 'Error',
        }
        return <span className={`badge badge-${status}`}>{labels[status] || status}</span>
    }

    // Get unpaired ebooks and audiobooks for the modal
    const pairedEbookIds = new Set(pairs.map(p => p.ebook.id))
    const pairedAudiobookIds = new Set(pairs.map(p => p.audiobook.id))
    const unpairedEbooks = ebooks.filter(e => !pairedEbookIds.has(e.id))
    const unpairedAudiobooks = audiobooks.filter(a => !pairedAudiobookIds.has(a.id))

    if (loading) {
        return <div className="loading-page"><div className="spinner"></div> Loading pairs...</div>
    }

    return (
        <div>
            <div className="page-header">
                <h2>Book Pairs</h2>
                <p>Matched ebook and audiobook pairs. Matching enables sync between reading and listening.</p>
            </div>

            {error && <div className="alert alert-error">⚠️ {error}</div>}

            <div style={{ marginBottom: '24px' }}>
                <button className="btn btn-primary" onClick={() => setShowModal(true)}>
                    ➕ Manual Match
                </button>
            </div>

            {pairs.length === 0 ? (
                <div className="card">
                    <div className="empty-state">
                        <div className="icon">🔗</div>
                        <h3>No book pairs yet</h3>
                        <p>
                            Scan your library to auto-match books, or manually pair an ebook
                            with its audiobook.
                        </p>
                    </div>
                </div>
            ) : (
                <div className="card-grid">
                    {pairs.map(pair => (
                        <div key={pair.id} className="card">
                            <div className="card-header">
                                {statusBadge(pair.status)}
                                <button
                                    className="btn btn-icon btn-danger btn-sm"
                                    onClick={() => handleDeletePair(pair.id)}
                                    title="Delete pair"
                                >
                                    🗑️
                                </button>
                            </div>
                            <div style={{ marginBottom: '12px' }}>
                                <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '4px' }}>
                                    EBook
                                </div>
                                <div style={{ fontWeight: 600, fontSize: '0.95rem' }}>
                                    📚 {pair.ebook.title}
                                </div>
                                <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                                    {pair.ebook.format} • {pair.ebook.author || 'Unknown author'}
                                </div>
                            </div>
                            <div style={{ marginBottom: '12px' }}>
                                <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '4px' }}>
                                    Audiobook
                                </div>
                                <div style={{ fontWeight: 600, fontSize: '0.95rem' }}>
                                    🎧 {pair.audiobook.title}
                                </div>
                                <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                                    {pair.audiobook.format} • {pair.audiobook.author || 'Unknown author'}
                                </div>
                            </div>
                            {pair.matched_at && (
                                <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '8px' }}>
                                    Matched: {new Date(pair.matched_at).toLocaleDateString()}
                                </div>
                            )}
                        </div>
                    ))}
                </div>
            )}

            {/* Manual Match Modal */}
            {showModal && (
                <div className="modal-overlay" onClick={() => setShowModal(false)}>
                    <div className="modal" onClick={e => e.stopPropagation()}>
                        <h3>Manual Match</h3>
                        <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', marginBottom: '20px' }}>
                            Select an ebook and its corresponding audiobook to create a pair.
                        </p>

                        <div className="form-group">
                            <label>EBook</label>
                            <select
                                className="form-input"
                                value={selectedEbook}
                                onChange={e => setSelectedEbook(e.target.value)}
                            >
                                <option value="">Select an ebook...</option>
                                {unpairedEbooks.map(ebook => (
                                    <option key={ebook.id} value={ebook.id}>
                                        {ebook.title} ({ebook.format})
                                    </option>
                                ))}
                            </select>
                        </div>

                        <div className="form-group">
                            <label>Audiobook</label>
                            <select
                                className="form-input"
                                value={selectedAudiobook}
                                onChange={e => setSelectedAudiobook(e.target.value)}
                            >
                                <option value="">Select an audiobook...</option>
                                {unpairedAudiobooks.map(ab => (
                                    <option key={ab.id} value={ab.id}>
                                        {ab.title} ({ab.format})
                                    </option>
                                ))}
                            </select>
                        </div>

                        <div className="modal-actions">
                            <button className="btn btn-secondary" onClick={() => setShowModal(false)}>
                                Cancel
                            </button>
                            <button
                                className="btn btn-primary"
                                onClick={handleCreatePair}
                                disabled={!selectedEbook || !selectedAudiobook}
                            >
                                Create Pair
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    )
}

export default PairsPage
