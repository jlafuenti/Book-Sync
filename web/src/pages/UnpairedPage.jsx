import React, { useState, useEffect, useMemo } from 'react'
import { getUnpairedMedia, createPair } from '../api'
import CoverImg from '../components/CoverImg'

function matchesFilters(item, text, author, series) {
    if (author && item.author !== author) return false
    if (series && item.series !== series) return false
    if (text) {
        const term = text.toLowerCase()
        if (
            !item.title?.toLowerCase().includes(term) &&
            !item.author?.toLowerCase().includes(term) &&
            !item.series?.toLowerCase().includes(term)
        ) return false
    }
    return true
}

function uniqueSorted(items, field) {
    return [...new Set(items.map(i => i[field]).filter(Boolean))].sort((a, b) =>
        a.localeCompare(b, undefined, { sensitivity: 'base' })
    )
}

function FilterBar({ text, onText, author, onAuthor, series, onSeries, authors, seriesList, textPlaceholder, onClear }) {
    return (
        <div className="unpaired-filter-bar">
            <input
                className="form-input"
                placeholder={textPlaceholder}
                value={text}
                onChange={e => onText(e.target.value)}
            />
            <select
                className="form-input unpaired-filter-select"
                value={author}
                onChange={e => onAuthor(e.target.value)}
            >
                <option value="">All authors</option>
                {authors.map(a => <option key={a} value={a}>{a}</option>)}
            </select>
            <select
                className="form-input unpaired-filter-select"
                value={series}
                onChange={e => onSeries(e.target.value)}
            >
                <option value="">All series</option>
                {seriesList.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
            {(text || author || series) && (
                <button className="btn btn-secondary btn-sm" onClick={onClear} style={{ flexShrink: 0 }}>Clear</button>
            )}
        </div>
    )
}

export function BookRow({ item, selected, onSelect }) {
    return (
        <div
            className={`unpaired-row${selected ? ' unpaired-row-selected' : ''}`}
            onClick={() => onSelect(selected ? null : item)}
        >
            <div className="unpaired-row-cover">
                {item.cover_path
                    ? <CoverImg path={item.cover_path} alt="" />
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
    // Only the unpaired set is fetched (issue #120) — bounded by
    // `tab=unpaired`, not the whole library set-diffed against every pair.
    const [unpairedEbooks, setUnpairedEbooks] = useState([])
    const [unpairedAudiobooks, setUnpairedAudiobooks] = useState([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState('')

    // Global filter (applies to both sides)
    const [globalText, setGlobalText] = useState('')
    const [globalAuthor, setGlobalAuthor] = useState('')
    const [globalSeries, setGlobalSeries] = useState('')

    // Per-side filters (applied on top of global)
    const [ebookText, setEbookText] = useState('')
    const [ebookAuthor, setEbookAuthor] = useState('')
    const [ebookSeries, setEbookSeries] = useState('')

    const [audiobookText, setAudiobookText] = useState('')
    const [audiobookAuthor, setAudiobookAuthor] = useState('')
    const [audiobookSeries, setAudiobookSeries] = useState('')

    const [selectedEbook, setSelectedEbook] = useState(null)
    const [selectedAudiobook, setSelectedAudiobook] = useState(null)
    const [pairing, setPairing] = useState(false)
    const [pairError, setPairError] = useState('')

    const [ebookSideOpen, setEbookSideOpen] = useState(true)
    const [audiobookSideOpen, setAudiobookSideOpen] = useState(true)

    const loadData = async () => {
        try {
            const { ebooks, audiobooks } = await getUnpairedMedia()
            setUnpairedEbooks(ebooks)
            setUnpairedAudiobooks(audiobooks)
        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => { loadData() }, [])

    // Dropdown option lists — derived from all unpaired items (not filtered), so options don't disappear while filtering
    const globalAuthors = useMemo(() => uniqueSorted([...unpairedEbooks, ...unpairedAudiobooks], 'author'), [unpairedEbooks, unpairedAudiobooks])
    const globalSeriesList = useMemo(() => uniqueSorted([...unpairedEbooks, ...unpairedAudiobooks], 'series'), [unpairedEbooks, unpairedAudiobooks])
    const ebookAuthors = useMemo(() => uniqueSorted(unpairedEbooks, 'author'), [unpairedEbooks])
    const ebookSeriesList = useMemo(() => uniqueSorted(unpairedEbooks, 'series'), [unpairedEbooks])
    const audiobookAuthors = useMemo(() => uniqueSorted(unpairedAudiobooks, 'author'), [unpairedAudiobooks])
    const audiobookSeriesList = useMemo(() => uniqueSorted(unpairedAudiobooks, 'series'), [unpairedAudiobooks])

    const visibleEbooks = useMemo(() =>
        unpairedEbooks
            .filter(e => matchesFilters(e, globalText, globalAuthor, globalSeries))
            .filter(e => matchesFilters(e, ebookText, ebookAuthor, ebookSeries)),
        [unpairedEbooks, globalText, globalAuthor, globalSeries, ebookText, ebookAuthor, ebookSeries]
    )

    const visibleAudiobooks = useMemo(() =>
        unpairedAudiobooks
            .filter(a => matchesFilters(a, globalText, globalAuthor, globalSeries))
            .filter(a => matchesFilters(a, audiobookText, audiobookAuthor, audiobookSeries)),
        [unpairedAudiobooks, globalText, globalAuthor, globalSeries, audiobookText, audiobookAuthor, audiobookSeries]
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
                <span className="unpaired-filter-label">Global filter</span>
                <FilterBar
                    text={globalText} onText={setGlobalText}
                    author={globalAuthor} onAuthor={setGlobalAuthor}
                    series={globalSeries} onSeries={setGlobalSeries}
                    authors={globalAuthors} seriesList={globalSeriesList}
                    textPlaceholder="Filter both sides by title, author, or series…"
                    onClear={() => { setGlobalText(''); setGlobalAuthor(''); setGlobalSeries('') }}
                />
            </div>

            <div className="unpaired-columns">
                {/* Left: Ebooks */}
                {ebookSideOpen ? (
                    <div className="unpaired-column">
                        <div className="unpaired-column-header">
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                <h3>📚 Unpaired Ebooks <span className="unpaired-count">{visibleEbooks.length}</span></h3>
                                <button
                                    className="btn btn-secondary btn-sm"
                                    style={{ padding: '4px 10px', fontSize: '0.8rem' }}
                                    onClick={() => { setEbookSideOpen(false); setSelectedEbook(null) }}
                                    title="Hide ebooks panel"
                                >
                                    ⟨ Hide
                                </button>
                            </div>
                            <FilterBar
                                text={ebookText} onText={setEbookText}
                                author={ebookAuthor} onAuthor={setEbookAuthor}
                                series={ebookSeries} onSeries={setEbookSeries}
                                authors={ebookAuthors} seriesList={ebookSeriesList}
                                textPlaceholder="Filter ebooks…"
                                onClear={() => { setEbookText(''); setEbookAuthor(''); setEbookSeries('') }}
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
                ) : (
                    <div
                        style={{
                            display: 'flex', flexDirection: 'column', alignItems: 'center',
                            justifyContent: 'center', padding: '24px 12px',
                            background: 'var(--bg-card)', border: '1px dashed var(--border)',
                            borderRadius: '8px', cursor: 'pointer', minWidth: '80px'
                        }}
                        onClick={() => setEbookSideOpen(true)}
                    >
                        <span style={{ fontSize: '1.5rem' }}>📚</span>
                        <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '8px', writingMode: 'vertical-rl' }}>
                            Ebooks hidden
                        </span>
                        <button
                            className="btn btn-secondary btn-sm"
                            style={{ marginTop: '12px', padding: '4px 8px', fontSize: '0.8rem' }}
                        >
                            ⟩ Show
                        </button>
                    </div>
                )}

                {ebookSideOpen && audiobookSideOpen && <div className="unpaired-column-divider" />}

                {/* Right: Audiobooks */}
                {audiobookSideOpen ? (
                    <div className="unpaired-column">
                        <div className="unpaired-column-header">
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                <h3>🎧 Unpaired Audiobooks <span className="unpaired-count">{visibleAudiobooks.length}</span></h3>
                                <button
                                    className="btn btn-secondary btn-sm"
                                    style={{ padding: '4px 10px', fontSize: '0.8rem' }}
                                    onClick={() => { setAudiobookSideOpen(false); setSelectedAudiobook(null) }}
                                    title="Hide audiobooks panel"
                                >
                                    ⟨ Hide
                                </button>
                            </div>
                            <FilterBar
                                text={audiobookText} onText={setAudiobookText}
                                author={audiobookAuthor} onAuthor={setAudiobookAuthor}
                                series={audiobookSeries} onSeries={setAudiobookSeries}
                                authors={audiobookAuthors} seriesList={audiobookSeriesList}
                                textPlaceholder="Filter audiobooks…"
                                onClear={() => { setAudiobookText(''); setAudiobookAuthor(''); setAudiobookSeries('') }}
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
                ) : (
                    <div
                        style={{
                            display: 'flex', flexDirection: 'column', alignItems: 'center',
                            justifyContent: 'center', padding: '24px 12px',
                            background: 'var(--bg-card)', border: '1px dashed var(--border)',
                            borderRadius: '8px', cursor: 'pointer', minWidth: '80px'
                        }}
                        onClick={() => setAudiobookSideOpen(true)}
                    >
                        <span style={{ fontSize: '1.5rem' }}>🎧</span>
                        <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '8px', writingMode: 'vertical-rl' }}>
                            Audiobooks hidden
                        </span>
                        <button
                            className="btn btn-secondary btn-sm"
                            style={{ marginTop: '12px', padding: '4px 8px', fontSize: '0.8rem' }}
                        >
                            ⟩ Show
                        </button>
                    </div>
                )}
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
