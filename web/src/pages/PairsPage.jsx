import React, { useState, useEffect, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { getPairs, getUnpairedMedia, createPair, deletePair, getAllProgress } from '../api'
import { pairTargetPath, pairSourceFromProgress } from '../utils/pairRouting'
import MetadataCleanupModal from '../components/MetadataCleanupModal'
import { useAuth } from '../contexts/AuthContext'

// Tri-state sort: null → 'asc' → 'desc' → null
function nextSortDir(current) {
    if (!current) return 'asc'
    if (current === 'asc') return 'desc'
    return null
}

function SortableHeader({ label, column, sortCol, sortDir, onSort, style }) {
    const isActive = sortCol === column
    const arrow = isActive ? (sortDir === 'asc' ? ' ▲' : ' ▼') : ''
    return (
        <th
            style={{
                cursor: 'pointer',
                userSelect: 'none',
                whiteSpace: 'nowrap',
                ...style,
            }}
            onClick={() => onSort(column)}
            title={isActive ? `Sorted ${sortDir} — click to ${sortDir === 'asc' ? 'sort descending' : 'remove sort'}` : 'Click to sort'}
        >
            {label}
            <span style={{ color: 'var(--accent)', fontSize: '0.75em', marginLeft: '2px' }}>{arrow}</span>
        </th>
    )
}

function PairsPage({ tab }) {
    const { hasMinRole } = useAuth()
    const canEdit = hasMinRole('editor')
    const [pairs, setPairs] = useState([])
    // Only the unpaired media are fetched (issue #120) — the manual-pairing
    // pickers need nothing else, and the whole-library walk is gone.
    const [unpairedEbooks, setUnpairedEbooks] = useState([])
    const [unpairedAudiobooks, setUnpairedAudiobooks] = useState([])
    // Progress records — they carry the pair's `bookmarks.source`, which is
    // what decides the medium a pair-tap opens (issue #215).
    const [progress, setProgress] = useState([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState('')

    // Modal state for pairing
    const [showModal, setShowModal] = useState(false)
    const [modalMode, setModalMode] = useState(null) // 'pair-ebook' or 'pair-audiobook'
    const [selectedItem, setSelectedItem] = useState(null) // the ebook or audiobook to pair
    const [selectedCounterpart, setSelectedCounterpart] = useState('')

    // Sort/filter state for paired table
    const [pairSort, setPairSort] = useState({ col: null, dir: null })
    const [searchTerm, setSearchTerm] = useState('')

    // Metadata cleanup modal
    const [showCleanupModal, setShowCleanupModal] = useState(false)

    const activeTab = tab || 'paired'

    const loadData = async () => {
        try {
            const [p, unpaired, prog] = await Promise.all([
                getPairs(), getUnpairedMedia(),
                getAllProgress().catch(() => []),
            ])
            setPairs(p)
            setUnpairedEbooks(unpaired.ebooks)
            setUnpairedAudiobooks(unpaired.audiobooks)
            setProgress(prog || [])
        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => { loadData() }, [])

    // Group progress records by pair id so we can read each pair's `source`
    // for routing (audiobook vs ebook reader). Falls back to id-based lookup
    // if a record predates the `book_pair_id` column.
    const lastFormatByPairId = useMemo(() => {
        const ebookToPairId = {}
        const audiobookToPairId = {}
        pairs.forEach(p => {
            if (p.ebook?.id) ebookToPairId[p.ebook.id] = p.id
            if (p.audiobook?.id) audiobookToPairId[p.audiobook.id] = p.id
        })
        const grouped = {}
        ;(progress || []).forEach(rec => {
            let pid = rec.book_pair_id
            if (!pid) {
                if (rec.media_type === 'ebook' && rec.ebook_id) pid = ebookToPairId[rec.ebook_id]
                if (rec.media_type === 'audiobook' && rec.audiobook_id) pid = audiobookToPairId[rec.audiobook_id]
            }
            if (!pid) return
            if (!grouped[pid]) grouped[pid] = []
            grouped[pid].push(rec)
        })
        const out = {}
        Object.entries(grouped).forEach(([pid, recs]) => {
            out[pid] = pairSourceFromProgress(recs)
        })
        return out
    }, [pairs, progress])

    // Sorting logic for pairs
    const handlePairSort = (col) => {
        setPairSort(prev => ({
            col: prev.col === col && prev.dir === 'desc' ? null : col,
            dir: prev.col === col ? nextSortDir(prev.dir) : 'asc'
        }))
    }

    const sortedPairs = useMemo(() => {
        let filtered = pairs
        if (searchTerm) {
            const term = searchTerm.toLowerCase()
            filtered = pairs.filter(p =>
                p.ebook.title?.toLowerCase().includes(term) ||
                p.audiobook.title?.toLowerCase().includes(term) ||
                p.ebook.author?.toLowerCase().includes(term) ||
                p.audiobook.author?.toLowerCase().includes(term)
            )
        }
        if (!pairSort.col || !pairSort.dir) return filtered

        const sorted = [...filtered].sort((a, b) => {
            let valA, valB
            if (pairSort.col === 'title') {
                valA = (a.ebook.title || '').toLowerCase()
                valB = (b.ebook.title || '').toLowerCase()
            } else if (pairSort.col === 'ebook') {
                valA = (a.ebook.filename || '').toLowerCase()
                valB = (b.ebook.filename || '').toLowerCase()
            } else if (pairSort.col === 'audiobook') {
                valA = (a.audiobook.filename || '').toLowerCase()
                valB = (b.audiobook.filename || '').toLowerCase()
            } else if (pairSort.col === 'status') {
                valA = (a.status || '').toLowerCase()
                valB = (b.status || '').toLowerCase()
            } else {
                return 0
            }
            if (valA < valB) return pairSort.dir === 'asc' ? -1 : 1
            if (valA > valB) return pairSort.dir === 'asc' ? 1 : -1
            return 0
        })
        return sorted
    }, [pairs, pairSort, searchTerm])

    const handleDeletePair = async (pairId) => {
        if (!confirm('Delete this book pair? This will remove the sync map but keeps the ebook and audiobook files.')) return
        try {
            await deletePair(pairId)
            await loadData()
        } catch (err) {
            setError(err.message)
        }
    }

    // Open pairing modal
    const openPairModal = (item, mode) => {
        setSelectedItem(item)
        setModalMode(mode)
        setSelectedCounterpart('')
        setShowModal(true)
    }

    const handleCreatePair = async () => {
        if (!selectedCounterpart) return
        setError('')
        try {
            if (modalMode === 'pair-ebook') {
                // selectedItem is an ebook, counterpart is audiobook
                await createPair(selectedItem.id, parseInt(selectedCounterpart))
            } else {
                // selectedItem is an audiobook, counterpart is ebook
                await createPair(parseInt(selectedCounterpart), selectedItem.id)
            }
            setShowModal(false)
            setSelectedItem(null)
            setSelectedCounterpart('')
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

            {/* ====== Paired Files Tab ====== */}
            {activeTab === 'paired' && (
                <div>
                    {/* Stats */}
                    <div className="stat-grid">
                        <div className="stat-card">
                            <div className="stat-icon green">🔗</div>
                            <div>
                                <div className="stat-value">{pairs.length}</div>
                                <div className="stat-label">Paired</div>
                            </div>
                        </div>
                        <div className="stat-card">
                            <div className="stat-icon purple">📚</div>
                            <div>
                                <div className="stat-value">{unpairedEbooks.length}</div>
                                <div className="stat-label">Unpaired Books</div>
                            </div>
                        </div>
                        <div className="stat-card">
                            <div className="stat-icon blue">🎧</div>
                            <div>
                                <div className="stat-value">{unpairedAudiobooks.length}</div>
                                <div className="stat-label">Unpaired Audiobooks</div>
                            </div>
                        </div>
                    </div>

                    {/* Search filter */}
                    <div className="card" style={{ marginBottom: '24px', padding: '20px' }}>
                        <div style={{ display: 'flex', gap: '15px', flexWrap: 'wrap' }}>
                            <div style={{ flex: 1, minWidth: '200px' }}>
                                <label style={{ display: 'block', marginBottom: '5px', fontSize: '0.9rem', fontWeight: 500 }}>Search</label>
                                <input
                                    type="text"
                                    placeholder="Search by title, author..."
                                    className="form-input"
                                    value={searchTerm}
                                    onChange={e => setSearchTerm(e.target.value)}
                                />
                            </div>
                            <div style={{ display: 'flex', alignItems: 'flex-end' }}>
                                <button
                                    className="btn btn-secondary"
                                    onClick={() => setSearchTerm('')}
                                    disabled={!searchTerm}
                                >
                                    Clear
                                </button>
                                {canEdit && (
                                    <button
                                        className="btn btn-primary"
                                        style={{ marginLeft: '10px' }}
                                        onClick={() => setShowCleanupModal(true)}
                                    >
                                        🧹 Clean Up Metadata
                                    </button>
                                )}
                            </div>
                        </div>
                    </div>

                    {sortedPairs.length === 0 ? (
                        <div className="card">
                            <div className="empty-state">
                                <div className="icon">🔗</div>
                                <h3>{pairs.length === 0 ? 'No book pairs yet' : 'No matching pairs'}</h3>
                                <p>
                                    {pairs.length === 0
                                        ? 'Scan your library to auto-match books, or manually pair an ebook with its audiobook.'
                                        : 'Try adjusting your search term.'}
                                </p>
                            </div>
                        </div>
                    ) : (
                        <div className="card">
                            <div className="card-header">
                                <h3>🔗 Paired Files ({sortedPairs.length})</h3>
                            </div>
                            <div className="table-wrapper">
                                <table>
                                    <thead>
                                        <tr>
                                            <SortableHeader label="Title" column="title" sortCol={pairSort.col} sortDir={pairSort.dir} onSort={handlePairSort} />
                                            <SortableHeader label="Ebook" column="ebook" sortCol={pairSort.col} sortDir={pairSort.dir} onSort={handlePairSort} />
                                            <SortableHeader label="Audiobook" column="audiobook" sortCol={pairSort.col} sortDir={pairSort.dir} onSort={handlePairSort} />
                                            <SortableHeader label="Status" column="status" sortCol={pairSort.col} sortDir={pairSort.dir} onSort={handlePairSort} />
                                            <th style={{ width: '60px' }}></th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {sortedPairs.map(pair => (
                                            <tr key={pair.id}>
                                                <td style={{ fontWeight: 500 }}>
                                                    <Link
                                                        to={pairTargetPath(
                                                            { ebook_id: pair.ebook.id, audiobook_id: pair.audiobook?.id },
                                                            lastFormatByPairId[pair.id],
                                                        )}
                                                        style={{ color: 'var(--accent)', textDecoration: 'none' }}
                                                    >
                                                        {pair.ebook.title}
                                                    </Link>
                                                </td>
                                                <td style={{ color: 'var(--text-secondary)' }}>
                                                    <span className="badge badge-auto_matched" style={{ marginRight: '6px' }}>{pair.ebook.format}</span>
                                                    {pair.ebook.author || 'Unknown author'}
                                                </td>
                                                <td style={{ color: 'var(--text-secondary)' }}>
                                                    <span className="badge badge-auto_matched" style={{ marginRight: '6px' }}>{pair.audiobook.format}</span>
                                                    {pair.audiobook.author || 'Unknown author'}
                                                </td>
                                                <td>{statusBadge(pair.status)}</td>
                                                <td>
                                                    {canEdit && (
                                                        <button
                                                            className="btn btn-sm btn-danger"
                                                            onClick={() => handleDeletePair(pair.id)}
                                                            title="Delete pair"
                                                            style={{ padding: '4px 8px', fontSize: '0.8rem' }}
                                                        >
                                                            🗑️
                                                        </button>
                                                    )}
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

            {/* ====== Unpaired Books Tab ====== */}
            {activeTab === 'unpaired-books' && (
                <div>
                    <div className="stat-grid">
                        <div className="stat-card">
                            <div className="stat-icon purple">📚</div>
                            <div>
                                <div className="stat-value">{unpairedEbooks.length}</div>
                                <div className="stat-label">Unpaired Books</div>
                            </div>
                        </div>
                    </div>

                    {unpairedEbooks.length === 0 ? (
                        <div className="card">
                            <div className="empty-state">
                                <div className="icon">✅</div>
                                <h3>All ebooks are paired</h3>
                                <p>Every ebook in your library has a matching audiobook.</p>
                            </div>
                        </div>
                    ) : (
                        <div className="card">
                            <div className="card-header">
                                <h3>📚 Unpaired Books ({unpairedEbooks.length})</h3>
                            </div>
                            <div className="table-wrapper">
                                <table>
                                    <thead>
                                        <tr>
                                            <th>Title</th>
                                            <th>Author</th>
                                            <th>Series</th>
                                            <th>Format</th>
                                            <th style={{ width: '140px' }}>Action</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {unpairedEbooks.map(ebook => (
                                            <tr key={ebook.id}>
                                                <td style={{ fontWeight: 500 }}>
                                                    <Link to={`/book/ebook/${ebook.id}`} style={{ color: 'var(--accent)', textDecoration: 'none' }}>
                                                        {ebook.title}
                                                    </Link>
                                                </td>
                                                <td style={{ color: 'var(--text-secondary)' }}>{ebook.author || '—'}</td>
                                                <td style={{ color: 'var(--text-secondary)' }}>
                                                    {ebook.series ? `${ebook.series}${ebook.series_index ? ` #${ebook.series_index}` : ''}` : '—'}
                                                </td>
                                                <td><span className="badge badge-auto_matched">{ebook.format}</span></td>
                                                <td>
                                                    {canEdit && (
                                                        <button
                                                            className="btn btn-sm btn-primary"
                                                            onClick={() => openPairModal(ebook, 'pair-ebook')}
                                                            disabled={unpairedAudiobooks.length === 0}
                                                            title={unpairedAudiobooks.length === 0 ? 'No unpaired audiobooks available' : 'Pair with an audiobook'}
                                                        >
                                                            🎧 Pair
                                                        </button>
                                                    )}
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

            {/* ====== Unpaired Audiobooks Tab ====== */}
            {activeTab === 'unpaired-audiobooks' && (
                <div>
                    <div className="stat-grid">
                        <div className="stat-card">
                            <div className="stat-icon blue">🎧</div>
                            <div>
                                <div className="stat-value">{unpairedAudiobooks.length}</div>
                                <div className="stat-label">Unpaired Audiobooks</div>
                            </div>
                        </div>
                    </div>

                    {unpairedAudiobooks.length === 0 ? (
                        <div className="card">
                            <div className="empty-state">
                                <div className="icon">✅</div>
                                <h3>All audiobooks are paired</h3>
                                <p>Every audiobook in your library has a matching ebook.</p>
                            </div>
                        </div>
                    ) : (
                        <div className="card">
                            <div className="card-header">
                                <h3>🎧 Unpaired Audiobooks ({unpairedAudiobooks.length})</h3>
                            </div>
                            <div className="table-wrapper">
                                <table>
                                    <thead>
                                        <tr>
                                            <th>Title</th>
                                            <th>Author</th>
                                            <th>Series</th>
                                            <th>Format</th>
                                            <th style={{ width: '140px' }}>Action</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {unpairedAudiobooks.map(ab => (
                                            <tr key={ab.id}>
                                                <td style={{ fontWeight: 500 }}>
                                                    <Link to={`/book/audiobook/${ab.id}`} style={{ color: 'var(--accent)', textDecoration: 'none' }}>
                                                        {ab.title}
                                                    </Link>
                                                </td>
                                                <td style={{ color: 'var(--text-secondary)' }}>{ab.author || '—'}</td>
                                                <td style={{ color: 'var(--text-secondary)' }}>
                                                    {ab.series ? `${ab.series}${ab.series_index ? ` #${ab.series_index}` : ''}` : '—'}
                                                </td>
                                                <td><span className="badge badge-auto_matched">{ab.format}</span></td>
                                                <td>
                                                    {canEdit && (
                                                        <button
                                                            className="btn btn-sm btn-primary"
                                                            onClick={() => openPairModal(ab, 'pair-audiobook')}
                                                            disabled={unpairedEbooks.length === 0}
                                                            title={unpairedEbooks.length === 0 ? 'No unpaired ebooks available' : 'Pair with an ebook'}
                                                        >
                                                            📚 Pair
                                                        </button>
                                                    )}
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

            {/* Pairing Modal */}
            {canEdit && showModal && selectedItem && (
                <div className="modal-overlay" onClick={() => setShowModal(false)}>
                    <div className="modal" onClick={e => e.stopPropagation()}>
                        <h3>
                            {modalMode === 'pair-ebook'
                                ? `Pair "${selectedItem.title}" with an Audiobook`
                                : `Pair "${selectedItem.title}" with an Ebook`}
                        </h3>
                        <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', marginBottom: '20px' }}>
                            {modalMode === 'pair-ebook'
                                ? 'Select an audiobook to pair with this ebook.'
                                : 'Select an ebook to pair with this audiobook.'}
                        </p>

                        <div className="form-group">
                            <label>{modalMode === 'pair-ebook' ? 'Audiobook' : 'Ebook'}</label>
                            <select
                                className="form-input"
                                value={selectedCounterpart}
                                onChange={e => setSelectedCounterpart(e.target.value)}
                            >
                                <option value="">
                                    {modalMode === 'pair-ebook' ? 'Select an audiobook...' : 'Select an ebook...'}
                                </option>
                                {modalMode === 'pair-ebook'
                                    ? unpairedAudiobooks.map(ab => (
                                        <option key={ab.id} value={ab.id}>
                                            {ab.title} ({ab.format}){ab.author ? ` — ${ab.author}` : ''}
                                        </option>
                                    ))
                                    : unpairedEbooks.map(eb => (
                                        <option key={eb.id} value={eb.id}>
                                            {eb.title} ({eb.format}){eb.author ? ` — ${eb.author}` : ''}
                                        </option>
                                    ))
                                }
                            </select>
                        </div>

                        <div className="modal-actions">
                            <button className="btn btn-secondary" onClick={() => setShowModal(false)}>
                                Cancel
                            </button>
                            <button
                                className="btn btn-primary"
                                onClick={handleCreatePair}
                                disabled={!selectedCounterpart}
                            >
                                Create Pair
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {showCleanupModal && (
                <MetadataCleanupModal
                    onClose={() => setShowCleanupModal(false)}
                    onComplete={() => {
                        setShowCleanupModal(false);
                        loadData();
                    }}
                />
            )}
        </div>
    )
}

export default PairsPage
