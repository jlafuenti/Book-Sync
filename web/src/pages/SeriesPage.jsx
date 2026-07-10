import React, { useState, useEffect, useMemo, useRef, useCallback } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { getEbooks, getAudiobooks, getPairs, updateEbookMetadata, updateAudiobookMetadata } from '../api'
import { useAuth } from '../contexts/AuthContext'
import useIsMobile from '../hooks/useIsMobile'
import FilterPill from '../components/FilterPill'
import CoverImg from '../components/CoverImg'
import './SeriesPage.css'

const SORT_LABELS = { name: 'Name', count: 'Book Count', recent: 'Recently Added' }
const prefixRe = /^(the|a|an)\s+/i

export default function SeriesPage() {
    const navigate = useNavigate()
    const [searchParams, setSearchParams] = useSearchParams()
    const { hasMinRole } = useAuth()
    const canEdit = hasMinRole('editor')
    const isMobile = useIsMobile()
    const [mobileSearch, setMobileSearch] = useState('')
    const [mobileFilterOpen, setMobileFilterOpen] = useState(false)
    const mobileFilterRef = useRef(null)

    // Data
    const [ebooks, setEbooks] = useState([])
    const [audiobooks, setAudiobooks] = useState([])
    const [pairs, setPairs] = useState([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState('')

    // View state
    const [viewMode, setViewMode] = useState('grid')
    const [activeFilter, setActiveFilter] = useState('all')
    const [authorFilter, setAuthorFilter] = useState('')
    const [seriesFilter, setSeriesFilter] = useState('')
    const [sortKey, setSortKey] = useState('name')
    const [sortDir, setSortDir] = useState('asc')
    const [sortOpen, setSortOpen] = useState(false)
    const [searchTerm, setSearchTerm] = useState('')
    const [unsortedOpen, setUnsortedOpen] = useState(false)

    // List view: track which series rows are expanded
    const [expandedRows, setExpandedRows] = useState(new Set())

    // Select mode
    const [selectMode, setSelectMode] = useState(false)
    const [selectedKeys, setSelectedKeys] = useState(new Set())

    // Bulk edit
    const [bulkEditOpen, setBulkEditOpen] = useState(false)
    const [bulkEditFields, setBulkEditFields] = useState({ author: '', series: '', series_index: '', publisher: '', published_year: '' })
    const [bulkSaving, setBulkSaving] = useState(false)

    const sortRef = useRef(null)

    // Sync searchTerm from URL param — global search bar owns it, don't clear
    useEffect(() => {
        setSearchTerm(searchParams.get('search') || '')
    }, [searchParams]) // eslint-disable-line react-hooks/exhaustive-deps

    // Close dropdowns on outside click
    useEffect(() => {
        const handler = (e) => {
            if (sortRef.current && !sortRef.current.contains(e.target)) setSortOpen(false)
            if (mobileFilterRef.current && !mobileFilterRef.current.contains(e.target)) setMobileFilterOpen(false)
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

    // Options scoped to current activeFilter (before author/series pill filters)
    const baseSeriesForOptions = useMemo(() =>
        seriesGroups.filter(g => {
            if (activeFilter === 'ebooks')    return g.items.every(i => i.type === 'ebook')
            if (activeFilter === 'audiobooks') return g.items.every(i => i.type === 'audiobook')
            return true
        }),
    [seriesGroups, activeFilter])

    const allAuthors = useMemo(() =>
        [...new Set(baseSeriesForOptions.map(g => g.author).filter(Boolean))].sort(),
    [baseSeriesForOptions])

    const allSeriesNames = useMemo(() =>
        baseSeriesForOptions.map(g => g.name).sort((a, b) => a.localeCompare(b)),
    [baseSeriesForOptions])

    // Filter & sort
    const filteredSeries = useMemo(() => {
        const effectiveSearch = searchTerm || mobileSearch
        const q = effectiveSearch.toLowerCase()

        let filtered = seriesGroups.filter(g => {
            if (q) {
                const hits = g.name.toLowerCase().includes(q) ||
                    (g.author && g.author.toLowerCase().includes(q)) ||
                    g.items.some(i => i.title.toLowerCase().includes(q))
                if (!hits) return false
            }
            if (authorFilter && g.author !== authorFilter) return false
            if (seriesFilter && g.name !== seriesFilter) return false
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
    }, [seriesGroups, searchTerm, mobileSearch, authorFilter, seriesFilter, activeFilter, sortKey, sortDir])

    const filteredUnsorted = useMemo(() => {
        let list = unseriedItems
        if (searchTerm) {
            const q = searchTerm.toLowerCase()
            list = list.filter(i =>
                i.title.toLowerCase().includes(q) ||
                (i.author && i.author.toLowerCase().includes(q))
            )
        }
        if (authorFilter) {
            list = list.filter(i => i.author === authorFilter)
        }
        return list
    }, [unseriedItems, searchTerm, authorFilter])

    // Filter pill counts
    const filterCounts = useMemo(() => ({
        all: seriesGroups.length,
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

    function toggleRowExpand(name) {
        setExpandedRows(prev => {
            const next = new Set(prev)
            next.has(name) ? next.delete(name) : next.add(name)
            return next
        })
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

    // Navigate to library with explicit series filter
    function navigateToSeries(group) {
        if (selectMode) return
        navigate(`/library?series=${encodeURIComponent(group.name)}`)
    }

    // Navigate to library with explicit author filter
    function navigateToAuthorInLibrary(author) {
        navigate(`/library?author=${encodeURIComponent(author)}`)
    }

    const sortDirLabel = sortDir === 'asc' ? '↑' : '↓'

    if (loading) {
        return <div className="loading-page"><div className="spinner"></div> Loading series...</div>
    }

    const filterPills = [
        { key: 'all', label: 'All' },
        { key: 'ebooks', label: 'Ebooks Only' },
        { key: 'audiobooks', label: 'Audiobooks Only' },
    ]
    const activePill = filterPills.find(p => p.key === activeFilter) || filterPills[0]

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

                    {/* Author filter pill */}
                    <FilterPill
                        label="Author"
                        value={authorFilter}
                        options={allAuthors}
                        onChange={setAuthorFilter}
                    />

                    {/* Series filter pill */}
                    <FilterPill
                        label="Series"
                        value={seriesFilter}
                        options={allSeriesNames}
                        onChange={setSeriesFilter}
                    />

                    {/* Active search chip */}
                    {searchTerm && (
                        <div className="library-search-active">
                            <span>Search: <strong>{searchTerm}</strong></span>
                            <button className="library-search-clear" onClick={() => setSearchParams(prev => { const n = new URLSearchParams(prev); n.delete('search'); return n }, { replace: true })}>✕</button>
                        </div>
                    )}
                </div>

                <div className="series-toolbar-right">
                    {/* View toggle */}
                    <div className="library-view-toggle">
                        <button
                            className={`library-view-btn${viewMode === 'grid' ? ' active' : ''}`}
                            onClick={() => setViewMode('grid')}
                            title="Grid view"
                        >
                            <svg viewBox="0 0 24 24" fill="currentColor" width="16" height="16">
                                <rect x="3" y="3" width="7" height="7" rx="1" />
                                <rect x="14" y="3" width="7" height="7" rx="1" />
                                <rect x="3" y="14" width="7" height="7" rx="1" />
                                <rect x="14" y="14" width="7" height="7" rx="1" />
                            </svg>
                        </button>
                        <button
                            className={`library-view-btn${viewMode === 'list' ? ' active' : ''}`}
                            onClick={() => setViewMode('list')}
                            title="List view"
                        >
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="16" height="16">
                                <line x1="8" y1="6" x2="21" y2="6" />
                                <line x1="8" y1="12" x2="21" y2="12" />
                                <line x1="8" y1="18" x2="21" y2="18" />
                                <line x1="3" y1="6" x2="3.01" y2="6" />
                                <line x1="3" y1="12" x2="3.01" y2="12" />
                                <line x1="3" y1="18" x2="3.01" y2="18" />
                            </svg>
                        </button>
                    </div>

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

            {/* Mobile filter dropdown — replaces desktop filter pills toolbar */}
            {isMobile && (
                <div className="library-mobile-filter-wrap" ref={mobileFilterRef}>
                    <button
                        className={`library-mobile-filter-btn${mobileFilterOpen ? ' open' : ''}`}
                        onClick={() => setMobileFilterOpen(o => !o)}
                    >
                        {activePill.label}
                        <span style={{ color: 'var(--text-muted)', fontWeight: 400, fontSize: '0.8rem' }}>({filterCounts[activePill.key]})</span>
                        <span className="material-symbols-outlined">expand_more</span>
                    </button>
                    {mobileFilterOpen && (
                        <div className="library-mobile-filter-dropdown">
                            {filterPills.map(p => (
                                <button
                                    key={p.key}
                                    className={`library-mobile-filter-option${activeFilter === p.key ? ' active' : ''}`}
                                    onClick={() => { setActiveFilter(p.key); setMobileFilterOpen(false) }}
                                >
                                    {p.label}
                                    <span className="filter-opt-count">{filterCounts[p.key]}</span>
                                    {activeFilter === p.key && (
                                        <span className="material-symbols-outlined filter-opt-check">check</span>
                                    )}
                                </button>
                            ))}
                        </div>
                    )}
                </div>
            )}

            {/* Mobile search */}
            {isMobile && (
                <div className="series-mobile-search">
                    <span className="search-icon material-symbols-outlined" style={{ fontSize: 20 }}>search</span>
                    <input
                        type="text"
                        placeholder="Search series..."
                        value={mobileSearch}
                        onChange={e => setMobileSearch(e.target.value)}
                    />
                </div>
            )}

            {/* Mobile sort pills */}
            {isMobile && (
                <div className="series-mobile-sort-pills">
                    {[
                        { key: 'name', label: 'Name' },
                        { key: 'count', label: 'Book Count' },
                        { key: 'recent', label: 'Recent' },
                    ].map(opt => (
                        <button
                            key={opt.key}
                            className={`series-mobile-sort-pill${sortKey === opt.key ? ' active' : ''}`}
                            onClick={() => {
                                if (sortKey === opt.key) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
                                else { setSortKey(opt.key); setSortDir('asc') }
                            }}
                        >
                            {opt.label} {sortKey === opt.key ? (sortDir === 'asc' ? '↑' : '↓') : ''}
                        </button>
                    ))}
                </div>
            )}

            {/* Stats */}
            <div className="series-stats">
                <span>Showing <strong>{filteredSeries.length}</strong> series</span>
                <span><strong>{filteredSeries.reduce((a, g) => a + g.items.length, 0)}</strong> books</span>
            </div>

            {/* Card Grid / List */}
            {filteredSeries.length > 0 ? (
                (viewMode === 'grid' || isMobile) ? (
                    <div className="series-grid">
                        {filteredSeries.map(group => (
                            <SeriesCard
                                key={group.name}
                                group={group}
                                selectMode={selectMode}
                                selectedKeys={selectedKeys}
                                onToggleSelect={toggleSelect}
                                onStartSelect={() => setSelectMode(true)}
                                onClick={() => navigateToSeries(group)}
                                onAuthorClick={navigateToAuthorInLibrary}
                            />
                        ))}
                    </div>
                ) : (
                    <div className="series-list">
                        {filteredSeries.map(group => (
                            <SeriesListRow
                                key={group.name}
                                group={group}
                                selectMode={selectMode}
                                selectedKeys={selectedKeys}
                                onToggleSelect={toggleSelect}
                                isExpanded={expandedRows.has(group.name)}
                                onToggleExpand={() => toggleRowExpand(group.name)}
                                onSeriesClick={() => navigateToSeries(group)}
                                onAuthorClick={navigateToAuthorInLibrary}
                            />
                        ))}
                    </div>
                )
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

// ---- Series Card (Grid) ----

export function SeriesCard({ group, selectMode, selectedKeys, onToggleSelect, onStartSelect, onClick, onAuthorClick }) {
    const { name, author, items, covers } = group

    const ebookCount = items.filter(i => i.hasEbook).length
    const audioCount = items.filter(i => i.hasAudiobook).length
    const pairedCount = items.filter(i => i.type === 'pair').length

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
            className={`series-card${anySelected ? ' selected' : ''}${selectMode ? ' select-mode' : ''}`}
            onClick={selectMode ? undefined : onClick}
        >
            <input
                type="checkbox"
                className="series-card-checkbox"
                checked={allSelected}
                readOnly
                onClick={(e) => {
                    if (selectMode) handleCheckbox(e)
                    else { e.stopPropagation(); onStartSelect(e); handleCheckbox(e) }
                }}
            />

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
                            <CoverImg key={i} path={c} className="series-cover-img" alt="" loading="lazy" draggable={false} />
                        ))}
                    </div>
                )}
            </div>

            <div className="series-card-body">
                <div className="series-card-title" title={name}>{name}</div>
                {author && (
                    <div
                        className="series-card-author series-card-author-link"
                        onClick={e => { e.stopPropagation(); onAuthorClick(author) }}
                        title={`View ${author} in Library`}
                    >
                        {author}
                    </div>
                )}
                <div className="series-card-stats">
                    {ebookCount > 0 && (
                        <span className="series-stat">📚 {ebookCount} ebook{ebookCount !== 1 ? 's' : ''}</span>
                    )}
                    {audioCount > 0 && (
                        <span className="series-stat">🎧 {audioCount} audiobook{audioCount !== 1 ? 's' : ''}</span>
                    )}
                    {pairedCount > 0 && (
                        <span className="series-stat">🔗 {pairedCount} paired</span>
                    )}
                </div>
            </div>
        </div>
    )
}

// ---- Series List Row (collapsible) ----

export function SeriesListRow({ group, selectMode, selectedKeys, onToggleSelect, isExpanded, onToggleExpand, onSeriesClick, onAuthorClick }) {
    const { name, author, items, covers } = group

    const ebookCount = items.filter(i => i.hasEbook).length
    const audioCount = items.filter(i => i.hasAudiobook).length
    const pairedCount = items.filter(i => i.type === 'pair').length

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

    return (
        <>
            <div className={`series-list-row${anySelected ? ' selected' : ''}`}>
                {/* Expand/collapse chevron */}
                <button
                    className="series-list-chevron-btn"
                    onClick={onToggleExpand}
                    title={isExpanded ? 'Collapse' : 'Expand'}
                >
                    <svg
                        className={`series-list-chevron${isExpanded ? ' open' : ''}`}
                        viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" width="14" height="14"
                    >
                        <polyline points="9 18 15 12 9 6" />
                    </svg>
                </button>

                {selectMode && (
                    <div onClick={e => e.stopPropagation()}>
                        <input
                            type="checkbox"
                            checked={allSelected}
                            readOnly
                            onClick={handleCheckbox}
                            style={{ cursor: 'pointer', accentColor: 'var(--accent)' }}
                        />
                    </div>
                )}

                {/* Thumbnail */}
                <div className="series-list-thumb" onClick={onSeriesClick} style={{ cursor: 'pointer' }}>
                    {covers.length > 0 ? (
                        <CoverImg path={covers[0]} alt="" className="series-list-thumb-img" loading="lazy" />
                    ) : (
                        <div className="series-list-thumb-placeholder">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" width="20" height="20">
                                <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
                                <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
                            </svg>
                        </div>
                    )}
                </div>

                {/* Title + author */}
                <div className="series-list-info" onClick={onSeriesClick} style={{ cursor: 'pointer' }}>
                    <div className="series-list-name">{name}</div>
                    {author && (
                        <div
                            className="series-list-author series-card-author-link"
                            onClick={e => { e.stopPropagation(); onAuthorClick(author) }}
                            title={`View ${author} in Library`}
                        >
                            {author}
                        </div>
                    )}
                </div>

                {/* Stats */}
                <div className="series-list-stats">
                    {ebookCount > 0 && <span className="series-list-stat">📚 {ebookCount}</span>}
                    {audioCount > 0 && <span className="series-list-stat">🎧 {audioCount}</span>}
                    {pairedCount > 0 && <span className="series-list-stat">🔗 {pairedCount} paired</span>}
                </div>
            </div>

            {/* Expanded book rows */}
            {isExpanded && items.map(item => (
                <SeriesBookRow key={item.key} item={item} />
            ))}
        </>
    )
}

// ---- Individual book row within expanded series ----

export function SeriesBookRow({ item }) {
    const typeIcon = item.type === 'pair'
        ? <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="13" height="13"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>
        : item.type === 'audiobook'
            ? <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="13" height="13"><path d="M3 18v-6a9 9 0 0 1 18 0v6"/><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3z"/><path d="M3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"/></svg>
            : <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="13" height="13"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>

    return (
        <div className="series-book-row">
            <div className="series-book-row-thumb">
                {item.cover_path ? (
                    <CoverImg path={item.cover_path} alt="" className="series-list-thumb-img" loading="lazy" />
                ) : (
                    <div className="series-list-thumb-placeholder" style={{ fontSize: '0.8rem' }}>📖</div>
                )}
            </div>
            <span className="series-book-row-type" title={item.type}>{typeIcon}</span>
            <span className="series-book-row-index">{item.seriesIndex != null ? `#${item.seriesIndex}` : ''}</span>
            <span className="series-book-row-title">{item.title}</span>
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
