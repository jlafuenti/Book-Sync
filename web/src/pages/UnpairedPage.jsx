import React, { useState, useEffect, useMemo } from 'react'
import { getPairs, getEbooks, getAudiobooks, createPair } from '../api'

function matchesFilter(item, filter) {
    if (!filter) return true
    const term = filter.toLowerCase()
    return (
        item.title?.toLowerCase().includes(term) ||
        item.author?.toLowerCase().includes(term) ||
        item.series?.toLowerCase().includes(term)
    )
}

function BookRow({ item, selected, onSelect }) {
    return (
        <div
            className={`unpaired-row${selected ? ' unpaired-row-selected' : ''}`}
            onClick={() => onSelect(selected ? null : item)}
        >
            <div className="unpaired-row-cover">
                {item.cover_path
                    ? <img src={item.cover_path} alt="" />
                    : <span className="unpaired-row-cover-placeholder">📄</span>
                }
            </div>
            <div className="unpaired-row-info">
                <div className="unpaired-row-title">{item.title || item.filename}</div>
                {item.author && <div className="unpaired-row-author">{item.author}</div>}
                {item.series && (
                    <div className="unpaired-row-series">
                        {item.series}{item.series_index != null ? ` #${item.series_index}` : ''}
                    </div>
                )}
            </div>
            <span className="badge">{item.format?.toUpperCase()}</span>
        </div>
    )
}

export default function UnpairedPage() {
    const [pairs, setPairs] = useState([])
    const [ebooks, setEbooks] = useState([])
    const [audiobooks, setAudiobooks] = useState([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState('')

    const [globalFilter, setGlobalFilter] = useState('')
    const [ebookFilter, setEbookFilter] = useState('')
    const [audiobookFilter, setAudiobookFilter] = useState('')

    const [selectedEbook, setSelectedEbook] = useState(null)
    const [selectedAudiobook, setSelectedAudiobook] = useState(null)
    const [pairing, setPairing] = useState(false)
    const [pairError, setPairError] = useState('')

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

    const pairedEbookIds = useMemo(() => new Set(pairs.map(p => p.ebook.id)), [pairs])
    const pairedAudiobookIds = useMemo(() => new Set(pairs.map(p => p.audiobook.id)), [pairs])

    const visibleEbooks = useMemo(() =>
        ebooks
            .filter(e => !pairedEbookIds.has(e.id))
            .filter(e => matchesFilter(e, globalFilter))
            .filter(e => matchesFilter(e, ebookFilter)),
        [ebooks, pairedEbookIds, globalFilter, ebookFilter]
    )

    const visibleAudiobooks = useMemo(() =>
        audiobooks
            .filter(a => !pairedAudiobookIds.has(a.id))
            .filter(a => matchesFilter(a, globalFilter))
            .filter(a => matchesFilter(a, audiobookFilter)),
        [audiobooks, pairedAudiobookIds, globalFilter, audiobookFilter]
    )

    const handlePair = async () => {
        if (!selectedEbook || !selectedAudiobook) return
        setPairing(true)
        setPairError('')
        try {
            await createPair(selectedEbook.id, selectedAudiobook.id)
            setSelectedEbook(null)
            setSelectedAudiobook(null)
            await loadData()
        } catch (err) {
            setPairError(err.message || 'Failed to create pair')
        } finally {
            setPairing(false)
        }
    }

    if (loading) {
        return (
            <div className="loading-page" style={{ minHeight: 'auto', flex: 1 }}>
                <div className="spinner"></div>
                <span>Loading unpaired items...</span>
            </div>
        )
    }

    if (error) {
        return (
            <div className="empty-state">
                <div className="icon">⚠️</div>
                <h3>Failed to load</h3>
                <p>{error}</p>
            </div>
        )
    }

    return (
        <div className="unpaired-page">
            <div className="unpaired-global-filter">
                <div className="unpaired-global-filter-inner">
                    <span className="unpaired-filter-label">Global filter</span>
                    <input
                        className="form-input"
                        placeholder="Filter both sides by title, author, or series…"
                        value={globalFilter}
                        onChange={e => setGlobalFilter(e.target.value)}
                    />
                    {globalFilter && (
                        <button className="btn btn-secondary btn-sm" onClick={() => setGlobalFilter('')}>Clear</button>
                    )}
                </div>
            </div>

            <div className="unpaired-columns">
                {/* Left: Ebooks */}
                <div className="unpaired-column">
                    <div className="unpaired-column-header">
                        <h3>📚 Unpaired Ebooks <span className="unpaired-count">{visibleEbooks.length}</span></h3>
                        <input
                            className="form-input unpaired-local-filter"
                            placeholder="Filter ebooks…"
                            value={ebookFilter}
                            onChange={e => setEbookFilter(e.target.value)}
                        />
                    </div>
                    <div className="unpaired-column-list">
                        {visibleEbooks.length === 0 ? (
                            <div className="unpaired-empty">No unpaired ebooks match the current filters.</div>
                        ) : (
                            visibleEbooks.map(e => (
                                <BookRow
                                    key={e.id}
                                    item={e}
                                    selected={selectedEbook?.id === e.id}
                                    onSelect={setSelectedEbook}
                                />
                            ))
                        )}
                    </div>
                </div>

                <div className="unpaired-column-divider" />

                {/* Right: Audiobooks */}
                <div className="unpaired-column">
                    <div className="unpaired-column-header">
                        <h3>🎧 Unpaired Audiobooks <span className="unpaired-count">{visibleAudiobooks.length}</span></h3>
                        <input
                            className="form-input unpaired-local-filter"
                            placeholder="Filter audiobooks…"
                            value={audiobookFilter}
                            onChange={e => setAudiobookFilter(e.target.value)}
                        />
                    </div>
                    <div className="unpaired-column-list">
                        {visibleAudiobooks.length === 0 ? (
                            <div className="unpaired-empty">No unpaired audiobooks match the current filters.</div>
                        ) : (
                            visibleAudiobooks.map(a => (
                                <BookRow
                                    key={a.id}
                                    item={a}
                                    selected={selectedAudiobook?.id === a.id}
                                    onSelect={setSelectedAudiobook}
                                />
                            ))
                        )}
                    </div>
                </div>
            </div>

            {/* Bottom pair bar */}
            <div className="unpaired-bar">
                <div className="unpaired-bar-selections">
                    <span className="unpaired-bar-item">
                        <span className="unpaired-bar-label">Ebook:</span>
                        <span className="unpaired-bar-value">
                            {selectedEbook ? selectedEbook.title || selectedEbook.filename : <em>none selected</em>}
                        </span>
                    </span>
                    <span className="unpaired-bar-arrow">↔</span>
                    <span className="unpaired-bar-item">
                        <span className="unpaired-bar-label">Audiobook:</span>
                        <span className="unpaired-bar-value">
                            {selectedAudiobook ? selectedAudiobook.title || selectedAudiobook.filename : <em>none selected</em>}
                        </span>
                    </span>
                </div>
                <div className="unpaired-bar-actions">
                    {pairError && <span className="unpaired-bar-error">{pairError}</span>}
                    <button
                        className="btn btn-primary"
                        disabled={!selectedEbook || !selectedAudiobook || pairing}
                        onClick={handlePair}
                    >
                        {pairing ? 'Pairing…' : '🔗 Pair'}
                    </button>
                </div>
            </div>
        </div>
    )
}
