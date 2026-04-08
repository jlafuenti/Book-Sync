import React, { useState, useEffect, useMemo, useRef, useCallback } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { getEbooks, getAudiobooks, getPairs, updateEbookMetadata, updateAudiobookMetadata, coverSrc } from '../api'
import { useAuth } from '../contexts/AuthContext'
import './SeriesPage.css'

const SORT_LABELS = { name: 'Name', count: 'Book Count', recent: 'Recently Added' }
const prefixRe = /^(the|a|an)\s+/i

export default function SeriesPage() {
    const navigate = useNavigate()
    const [searchParams, setSearchParams] = useSearchParams()
    const { hasMinRole } = useAuth()
    const canEdit = hasMinRole('editor')

    // Data
    const [ebooks, setEbooks] = useState([])
    const [audiobooks, setAudiobooks] = useState([])
    const [pairs, setPairs] = useState([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState('')

    // View state
    const [activeFilter, setActiveFilter] = useState('all')
    const [sortKey, setSortKey] = useState('name')
    const [sortDir, setSortDir] = useState('asc')
    const [sortOpen, setSortOpen] = useState(false)
    const [searchTerm, setSearchTerm] = useState('')
    const [unsortedOpen, setUnsortedOpen] = useState(false)

    // Select mode
    const [selectMode, setSelectMode] = useState(false)
    const [selectedKeys, setSelectedKeys] = useState(new Set())

    // Bulk edit
    const [bulkEditOpen, setBulkEditOpen] = useState(false)
    const [bulkEditFields, setBulkEditFields] = useState({ author: '', series: '', series_index: '', publisher: '', published_year: '' })
    const [bulkSaving, setBulkSaving] = useState(false)

    const sortRef = useRef(null)

    // URL search param (from global search bar)
    useEffect(() => {
        const urlSearch = searchParams.get('search')
        if (urlSearch) {
            setSearchTerm(urlSearch)
            setSearchParams({}, { replace: true })
        }
    }, [searchParams]) // eslint-disable-line react-hooks/exhaustive-deps

    // Close sort dropdown on outside click
    useEffect(() => {
        const handler = (e) => {
            if (sortRef.current && !sortRef.current.contains(e.target)) setSortOpen(false)
        }
        document.addEventListener('mousedown', handler)
        return () => document.removeEventListener('mousedown', handler)
    }, [])

    const loadAll = useCallback(async () => {
        setLoading(true)
        setError('')
        try {
            const [e, a, p] = await Promise.all([getEbooks(), getAudiobooks(), getPairs()])
            setEbooks(e)
            setAudiobooks(a)
            setPairs(p)
        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => { loadAll() }, [loadAll])

    // Build series groups
    const { seriesGroups, unseriedItems } = useMemo(() => {
        const pairedEbookIds = new Set(pairs.map(p => p.ebook.id))
        const pairedAudiobookIds = new Set(pairs.map(p => p.audiobook.id))

        const allItems = []

        pairs.forEach(p => {
            allItems.push({
                key: `pair_${p.id}`,
                type: 'pair',
                title: p.ebook.title,
                author: p.ebook.author || p.audiobook.author,
                series: p.ebook.series || p.audiobook.series,
                seriesIndex: p.ebook.series_index ?? p.audiobook.series_index,
                cover_path: p.ebook.cover_path || p.audiobook.cover_path,
                hasEbook: true,
                hasAudiobook: true,
                ebookFormat: p.ebook.format,
                audiobookFormat: p.audiobook.format,
                status: p.status,
                uploadedAt: p.ebook.uploaded_at,
                ebookId: p.ebook.id,
                audiobookId: p.audiobook.id,
                pairId: p.id,
            })
        })

        ebooks.filter(e => !pairedEbookIds.has(e.id)).forEach(e => {
            allItems.push({
                key: `ebook_${e.id}`,
                type: 'ebook',
                title: e.title,
                author: e.author,
                series: e.series,
                seriesIndex: e.series_index,
                cover_path: e.cover_path,
                hasEbook: true,
                hasAudiobook: false,
                ebookFormat: e.format,
                uploadedAt: e.uploaded_at,
                ebookId: e.id,
            })
        })

        audiobooks.filter(a => !pairedAudiobookIds.has(a.id)).forEach(a => {
            allItems.push({
                key: `audio_${a.id}`,
                type: 'audiobook',
                title: a.title,
                author: a.author,
                series: a.series,
                seriesIndex: a.series_index,
                cover_path: a.cover_path,
                hasEbook: false,
                hasAudiobook: true,
                audiobookFormat: a.format,
                uploadedAt: a.uploaded_at,
                audiobookId: a.id,
            })
        })

        const groups = {}
        const unsorted = []

        allItems.forEach(item => {
            if (item.series) {
                if (!groups[item.series]) {
                    groups[item.series] = {
                        name: item.series,
                        author: item.author,
                        items: [],
                        latestUpload: item.uploadedAt,
                        covers: [],
                    }
                }
                const g = groups[item.series]
                g.items.push(item)
                if (item.uploadedAt > g.latestUpload) g.latestUpload = item.uploadedAt
                if (!g.author && item.author) g.author = item.author
                // Collect up to 4 unique cover_paths (only real ones)
                if (item.cover_path && g.covers.length < 4 && !g.covers.includes(item.cover_path)) {
                    g.covers.push(item.cover_path)
                }
            } else {
                unsorted.push(item)
            }
        })

        Object.values(groups).forEach(g => {
            g.items.sort((a, b) => (a.seriesIndex ?? 999) - (b.seriesIndex ?? 999))
        })

        return { seriesGroups: Object.values(groups), unseriedItems: unsorted }
    }, [ebooks, audiobooks, pairs])

    // Filter & sort
    const filteredSeries = useMemo(() => {
        const q = searchTerm.toLowerCase()

        let filtered = seriesGroups.filter(g => {
            // Text search
            if (q) {
                const hits = g.name.toLowerCase().includes(q) ||
                    (g.author && g.author.toLowerCase().includes(q)) ||
                    g.items.some(i => i.title.toLowerCase().includes(q))
                if (!hits) return false
            }
            // Category filter
            if (activeFilter === 'paired') return g.items.every(i => i.type === 'pair')
            if (activeFilter === 'partial') return g.items.some(i => i.type === 'pair') && !g.items.every(i => i.type === 'pair')
            if (activeFilter === 'ebooks') return g.items.every(i => i.type === 'ebook')
            if (activeFilter === 'audiobooks') return g.items.every(i => i.type === 'audiobook')
            return true
        })

        filtered.sort((a, b) => {
            let cmp = 0
            if (sortKey === 'name') {
                cmp = a.name.replace(prefixRe, '').localeCompare(b.name.replace(prefixRe, ''))
            } else if (sortKey === 'count') {
                cmp = a.items.length - b.items.length
            } else if (sortKey === 'recent') {
                cmp = (a.latestUpload || '').localeCompare(b.latestUpload || '')
            }
            return sortDir === 'asc' ? cmp : -cmp
        })

        return filtered
    }, [seriesGroups, searchTerm, activeFilter, sortKey, sortDir])

    const filteredUnsorted = useMemo(() => {
        if (!searchTerm) return unseriedItems
        const q = searchTerm.toLowerCase()
        return unseriedItems.filter(i =>
            i.title.toLowerCase().includes(q) ||
            (i.author && i.author.toLowerCase().includes(q))
        )
    }, [unseriedItems, searchTerm])

    // Filter pill counts
    const filterCounts = useMemo(() => ({
        all: seriesGroups.length,
        paired: seriesGroups.filter(g => g.items.every(i => i.type === 'pair')).length,
        partial: seriesGroups.filter(g => g.items.some(i => i.type === 'pair') && !g.items.every(i => i.type === 'pair')).length,
        ebooks: seriesGroups.filter(g => g.items.every(i => i.type === 'ebook')).length,
        audiobooks: seriesGroups.filter(g => g.items.every(i => i.type === 'audiobook')).length,
    }), [seriesGroups])

    function toggleSelect(key) {
        setSelectedKeys(prev => {
            const next = new Set(prev)
            next.has(key) ? next.delete(key) : next.add(key)
            return next
        })
    }

    function exitSelectMode() {
        setSelectMode(false)
        setSelectedKeys(new Set())
    }

    const allVisibleItems = useMemo(() => {
        const items = []
        filteredSeries.forEach(g => g.items.forEach(i => items.push(i)))
        return items
    }, [filteredSeries])

    async function handleBulkEdit() {
        const patch = {}
        if (bulkEditFields.author.trim()) patch.author = bulkEditFields.author.trim()
        if (bulkEditFields.series.trim()) patch.series = bulkEditFields.series.trim()
        if (bulkEditFields.series_index.trim()) patch.series_index = parseFloat(bulkEditFields.series_index) || null
        if (bulkEditFields.publisher.trim()) patch.publisher = bulkEditFields.publisher.trim()
        if (bulkEditFields.published_year.trim()) patch.published_year = parseInt(bulkEditFields.published_year) || null
        if (Object.keys(patch).length === 0) return
        setBulkSaving(true)
        try {
            const selected = allVisibleItems.filter(i => selectedKeys.has(i.key))
            await Promise.all(selected.flatMap(item => {
                const calls = []
                if (item.ebookId) calls.push(updateEbookMetadata(item.ebookId, patch))
                if (item.audiobookId) calls.push(updateAudiobookMetadata(item.audiobookId, patch))
                return calls
            }))
            setBulkEditOpen(false)
            setBulkEditFields({ author: '', series: '', series_index: '', publisher: '', published_year: '' })
            exitSelectMode()
            await loadAll()
        } catch (err) {
            alert('Bulk edit failed: ' + err.message)
        } finally {
            setBulkSaving(false)
        }
    }

    function navigateToSeries(group) {
        if (selectMode) return
        const first = group.items[0]
        if (!first) return
        if (first.ebookId) navigate(`/book/ebook/${first.ebookId}`)
        else navigate(`/book/audiobook/${first.audiobookId}`)
    }

    const sortDirLabel = sortDir === 'asc' ? '↑' : '↓'

    if (loading) {
        return <div className="loading-page"><div className="spinner"></div> Loading series...</div>
    }

    const filterPills = [
        { key: 'all', label: 'All' },
        { key: 'paired', label: 'Fully Paired' },
        { key: 'partial', label: 'Partial' },
        { key: 'ebooks', label: 'Ebooks Only' },
        { key: 'audiobooks', label: 'Audiobooks Only' },
    ]

    return (
        <div className="series-page">
            {error && <div className="alert alert-error" style={{ marginBottom: 16 }}>{error}</div>}

            {/* Toolbar */}
            <div className="series-toolbar">
                <div className="series-toolbar-left">
                    {/* Filter pills */}
                    <div className="library-filter-pills">
                        {filterPills.map(p => (
                            <button
                                key={p.key}
                                className={`library-filter-pill${activeFilter === p.key ? ' active' : ''}`}
                                onClick={() => setActiveFilter(p.key)}
                            >
                                {p.label} ({filterCounts[p.key]})
                            </button>
                        ))}
                    </div>

                    {/* Active search chip */}
                    {searchTerm && (
                        <div className="library-search-active">
                            <span>Searching: <strong>{searchTerm}</strong></span>
                            <button className="library-search-clear" onClick={() => setSearchTerm('')}>✕</button>
                        </div>
                    )}
                </div>

                <div className="series-toolbar-right">
                    {/* Sort pill */}
                    <div className="series-sort-wrap" ref={sortRef}>
                        <button
                            className={`series-sort-btn${sortOpen ? ' open' : ''}`}
                            onClick={() => setSortOpen(o => !o)}
                        >
                            Sort: {SORT_LABELS[sortKey]} {sortDirLabel}
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="12" height="12">
                                <polyline points="6 9 12 15 18 9" />
                            </svg>
                        </button>
                        {sortOpen && (
                            <div className="series-sort-dropdown">
                                {[
                                    { key: 'name', label: 'Name' },
                                    { key: 'count', label: 'Book Count' },
                                    { key: 'recent', label: 'Recently Added' },
                                ].map(opt => (
                                    <button
                                        key={opt.key}
                                        className={`series-sort-option${sortKey === opt.key ? ' active' : ''}`}
                                        onClick={() => setSortKey(opt.key)}
                                    >
                                        {opt.label}
                                        {sortKey === opt.key && (
                                            <svg viewBox="0 0 24 24" fill="currentColor" width="14" height="14">
                                                <path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z" />
                                            </svg>
                                        )}
                                    </button>
                                ))}
                                <hr className="series-sort-divider" />
                                <div className="series-sort-dir-toggle">
                                    <button
                                        className={`series-sort-dir-btn${sortDir === 'asc' ? ' active' : ''}`}
                                        onClick={() => setSortDir('asc')}
                                    >↑ Ascending</button>
                                    <button
                                        className={`series-sort-dir-btn${sortDir === 'desc' ? ' active' : ''}`}
                                        onClick={() => setSortDir('desc')}
                                    >↓ Descending</button>
                                </div>
                            </div>
                        )}
                    </div>

                    {/* Select mode */}
                    {canEdit && (
                        <button
                            className={`btn ${selectMode ? 'btn-primary' : 'btn-secondary'}`}
                            onClick={() => selectMode ? exitSelectMode() : setSelectMode(true)}
                            style={{ fontSize: '0.85rem', padding: '6px 14px' }}
                        >
                            {selectMode ? `Cancel (${selectedKeys.size})` : 'Select'}
                        </button>
                    )}
                </div>
            </div>

            {/* Stats */}
            <div className="series-stats">
                <span>Showing <strong>{filteredSeries.length}</strong> series</span>
                <span><strong>{filteredSeries.reduce((a, g) => a + g.items.length, 0)}</strong> books</span>
            </div>

            {/* Card Grid */}
            {filteredSeries.length > 0 ? (
                <div className="series-grid">
                    {filteredSeries.map(group => (
                        <SeriesCard
                            key={group.name}
                            group={group}
                            selectMode={selectMode}
                            selectedKeys={selectedKeys}
                            onToggleSelect={toggleSelect}
                            onClick={() => navigateToSeries(group)}
                        />
                    ))}
                </div>
            ) : (
                <div className="series-empty">
                    <div className="series-empty-icon">📚</div>
                    <h3>No series found{searchTerm ? ` matching "${searchTerm}"` : ''}</h3>
                    <p>Try adjusting your search or filters.</p>
                </div>
            )}

            {/* Unsorted items section */}
            {filteredUnsorted.length > 0 && (
                <div>
                    <div className="series-unsorted-header" onClick={() => setUnsortedOpen(o => !o)}>
                        <svg
                            className={`series-unsorted-chevron${unsortedOpen ? ' open' : ''}`}
                            viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="14" height="14"
                        >
                            <polyline points="9 18 15 12 9 6" />
                        </svg>
                        <span className="series-unsorted-title">No Series</span>
                        <span className="series-unsorted-count">{filteredUnsorted.length}</span>
                    </div>
                    {unsortedOpen && (
                        <div className="series-unsorted-list">
                            {filteredUnsorted.map(item => (
                                <UnsortedRow
                                    key={item.key}
                                    item={item}
                                    onClick={() => {
                                        if (item.ebookId) navigate(`/book/ebook/${item.ebookId}`)
                                        else navigate(`/book/audiobook/${item.audiobookId}`)
                                    }}
                                />
                            ))}
                        </div>
                    )}
                </div>
            )}

            {/* Floating bulk action bar */}
            {canEdit && selectMode && selectedKeys.size > 0 && (
                <div style={{
                    position: 'fixed', bottom: '24px', left: '50%', transform: 'translateX(-50%)',
                    background: 'var(--bg-secondary)', border: '1px solid var(--border)',
                    borderRadius: '12px', padding: '12px 20px',
                    display: 'flex', alignItems: 'center', gap: '12px',
                    boxShadow: '0 8px 32px rgba(0,0,0,0.7)', zIndex: 100, whiteSpace: 'nowrap'
                }}>
                    <span style={{ fontWeight: 600, color: 'var(--text-secondary)' }}>
                        {selectedKeys.size} selected
                    </span>
                    <button className="btn btn-secondary" onClick={() => setBulkEditOpen(true)}>Edit Metadata</button>
                </div>
            )}

            {/* Bulk Edit Modal */}
            {canEdit && bulkEditOpen && (
                <div style={{
                    position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
                    background: 'rgba(0,0,0,0.6)', display: 'flex',
                    alignItems: 'center', justifyContent: 'center', zIndex: 1000
                }}>
                    <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: '12px', padding: '24px', maxWidth: '520px', width: '90%', boxShadow: '0 8px 32px rgba(0,0,0,0.6)' }}>
                        <h3 style={{ marginTop: 0 }}>Edit {selectedKeys.size} Item{selectedKeys.size !== 1 ? 's' : ''}</h3>
                        <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem', marginTop: 0 }}>
                            Leave fields blank to keep existing values.
                        </p>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                            {[
                                { key: 'author', label: 'Author', type: 'text' },
                                { key: 'series', label: 'Series', type: 'text' },
                                { key: 'series_index', label: 'Series Index', type: 'number' },
                                { key: 'publisher', label: 'Publisher', type: 'text' },
                                { key: 'published_year', label: 'Published Year', type: 'number' },
                            ].map(({ key, label, type }) => (
                                <div key={key}>
                                    <label style={{ display: 'block', marginBottom: '4px', fontSize: '0.9rem', fontWeight: 500 }}>{label}</label>
                                    <input
                                        className="form-input"
                                        type={type}
                                        placeholder="Leave blank to keep unchanged"
                                        value={bulkEditFields[key]}
                                        onChange={e => setBulkEditFields(prev => ({ ...prev, [key]: e.target.value }))}
                                    />
                                </div>
                            ))}
                        </div>
                        <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end', marginTop: '20px' }}>
                            <button className="btn btn-secondary" onClick={() => setBulkEditOpen(false)} disabled={bulkSaving}>Cancel</button>
                            <button
                                className="btn btn-primary"
                                onClick={handleBulkEdit}
                                disabled={bulkSaving || Object.values(bulkEditFields).every(v => !v.trim())}
                            >
                                {bulkSaving ? <><div className="spinner"></div> Saving...</> : `Save to ${selectedKeys.size} Item${selectedKeys.size !== 1 ? 's' : ''}`}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    )
}

// ---- Series Card ----

function SeriesCard({ group, selectMode, selectedKeys, onToggleSelect, onClick }) {
    const { name, author, items, covers } = group

    const pairedCount = items.filter(i => i.type === 'pair').length
    const ebookOnlyCount = items.filter(i => i.type === 'ebook').length
    const audioOnlyCount = items.filter(i => i.type === 'audiobook').length

    // Series range: min index to max index
    const indices = items.map(i => i.seriesIndex).filter(x => x != null)
    const rangeStr = indices.length > 0
        ? `BK ${Math.min(...indices)}–${Math.max(...indices)}`
        : `${items.length} ${items.length === 1 ? 'book' : 'books'}`

    // Select mode: any item in this series is selected?
    const groupKeys = items.map(i => i.key)
    const anySelected = groupKeys.some(k => selectedKeys.has(k))
    const allSelected = groupKeys.length > 0 && groupKeys.every(k => selectedKeys.has(k))

    function handleCheckbox(e) {
        e.stopPropagation()
        groupKeys.forEach(k => {
            const isSelected = selectedKeys.has(k)
            if (allSelected && isSelected) onToggleSelect(k)
            else if (!allSelected && !isSelected) onToggleSelect(k)
        })
    }

    const stackClass = `series-cover-stack${covers.length === 1 ? ' single' : ''}`

    return (
        <div
            className={`series-card${anySelected ? ' selected' : ''}`}
            onClick={selectMode ? undefined : onClick}
        >
            {selectMode && (
                <input
                    type="checkbox"
                    className="series-card-checkbox"
                    checked={allSelected}
                    readOnly
                    onClick={handleCheckbox}
                />
            )}

            {/* Cover stack */}
            <div className="series-cover-area">
                {covers.length === 0 ? (
                    <div className="series-cover-placeholder">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" width="40" height="40">
                            <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
                            <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
                        </svg>
                    </div>
                ) : (
                    <div className={stackClass}>
                        {covers.map((c, i) => (
                            <img
                                key={i}
                                className="series-cover-img"
                                src={coverSrc(c)}
                                alt=""
                                loading="lazy"
                                draggable={false}
                            />
                        ))}
                    </div>
                )}
            </div>

            {/* Card body */}
            <div className="series-card-body">
                <div className="series-card-title" title={name}>{name}</div>
                {author && <div className="series-card-author">{author}</div>}
                <div className="series-card-range">{rangeStr} &middot; {items.length} {items.length === 1 ? 'book' : 'books'}</div>
                <div className="series-card-stats">
                    {pairedCount > 0 && (
                        <span className="series-stat" title={`${pairedCount} paired`}>
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="11" height="11">
                                <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>
                                <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>
                            </svg>
                            {pairedCount}
                        </span>
                    )}
                    {ebookOnlyCount > 0 && (
                        <span className="series-stat" title={`${ebookOnlyCount} ebook only`}>
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="11" height="11">
                                <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/>
                                <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>
                            </svg>
                            {ebookOnlyCount}
                        </span>
                    )}
                    {audioOnlyCount > 0 && (
                        <span className="series-stat" title={`${audioOnlyCount} audiobook only`}>
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="11" height="11">
                                <path d="M3 18v-6a9 9 0 0 1 18 0v6"/>
                                <path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3z"/>
                                <path d="M3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"/>
                            </svg>
                            {audioOnlyCount}
                        </span>
                    )}
                </div>
            </div>
        </div>
    )
}

// ---- Unsorted Row ----

function UnsortedRow({ item, onClick }) {
    const typeIcon = item.type === 'pair'
        ? <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="13" height="13"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>
        : item.type === 'audiobook'
            ? <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="13" height="13"><path d="M3 18v-6a9 9 0 0 1 18 0v6"/><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3z"/><path d="M3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"/></svg>
            : <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="13" height="13"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>

    return (
        <div className="series-unsorted-row" onClick={onClick}>
            <span style={{ color: 'var(--text-muted)' }}>{typeIcon}</span>
            <span className="series-unsorted-row-title">{item.title}</span>
            {item.author && <span className="series-unsorted-row-author">{item.author}</span>}
        </div>
    )
}
