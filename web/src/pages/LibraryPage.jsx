import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
    getEbooks, getAudiobooks, getPairs, getNewPairs, uploadEbook, uploadAudiobook, scanLibrary,
    normalizeLibrary, updateEbookMetadata, updateAudiobookMetadata,
    rescanAllLibrary, deleteEbook, deleteAudiobook, verifyFiles,
    cleanupOrphans, coverSrc, acknowledgeNewItems, acknowledgeNewPairs
} from '../api'
import EnhancedMetadataModal from '../components/EnhancedMetadataModal'
import BulkMatchModal from '../components/BulkMatchModal'
import FilterPill from '../components/FilterPill'
import MetadataCleanupModal from '../components/MetadataCleanupModal'
import { useAuth } from '../contexts/AuthContext'
import useIsMobile from '../hooks/useIsMobile'
import './LibraryPage.css'

// ---- Sort helper ----

function sortComparator(sortKey) {
    return (a, b) => {
        let valA, valB
        const lastDash = sortKey.lastIndexOf('-')
        const field = sortKey.slice(0, lastDash)
        const dir = sortKey.slice(lastDash + 1)

        if (field === 'title' || field === 'author') {
            valA = (a[field] || '').toLowerCase()
            valB = (b[field] || '').toLowerCase()
            if (!a[field]) return 1
            if (!b[field]) return -1
        } else if (field === 'date') {
            valA = new Date(a.uploaded_at || 0).getTime()
            valB = new Date(b.uploaded_at || 0).getTime()
        } else if (field === 'series') {
            const sA = (a.series || '').toLowerCase()
            const sB = (b.series || '').toLowerCase()
            if (!a.series && !b.series) return 0
            if (!a.series) return 1
            if (!b.series) return -1
            if (sA !== sB) { valA = sA; valB = sB }
            else {
                valA = a.series_index ?? 999999
                valB = b.series_index ?? 999999
            }
        } else if (field === 'size') {
            valA = a.file_size ?? 0
            valB = b.file_size ?? 0
        } else {
            valA = (a[field] || '').toLowerCase()
            valB = (b[field] || '').toLowerCase()
        }

        if (valA < valB) return dir === 'asc' ? -1 : 1
        if (valA > valB) return dir === 'asc' ? 1 : -1
        return 0
    }
}

function formatSize(bytes) {
    if (!bytes) return '\u2014'
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / 1048576).toFixed(1)} MB`
}

// ---- Type badge icons ----

function TypeBadge({ mediaType }) {
    if (mediaType === 'pair') {
        return (
            <span className="lib-book-card-type-badge" title="Paired">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" width="12" height="12">
                    <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>
                    <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>
                </svg>
            </span>
        )
    }
    if (mediaType === 'audiobook') {
        return (
            <span className="lib-book-card-type-badge" title="Audiobook">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" width="12" height="12">
                    <path d="M3 18v-6a9 9 0 0 1 18 0v6"/>
                    <path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3z"/>
                    <path d="M3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"/>
                </svg>
            </span>
        )
    }
    // ebook
    return (
        <span className="lib-book-card-type-badge" title="Ebook">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" width="12" height="12">
                <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/>
                <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>
            </svg>
        </span>
    )
}

// ---- Book Card (Grid View) ----

function BookCard({ book, selectMode, isSelected, onSelect, onEdit, onDelete, onNavigate, canEdit }) {
    const [menuOpen, setMenuOpen] = useState(false)
    const menuRef = useRef(null)

    useEffect(() => {
        if (!menuOpen) return
        const handler = (e) => {
            if (menuRef.current && !menuRef.current.contains(e.target)) setMenuOpen(false)
        }
        window.addEventListener('mousedown', handler)
        return () => window.removeEventListener('mousedown', handler)
    }, [menuOpen])

    const coverUrl = coverSrc(book.cover_path)

    const handleClick = (e) => {
        if (selectMode) { onSelect(e); return }
        onNavigate()
    }

    return (
        <div
            className={`lib-book-card${isSelected ? ' selected' : ''}`}
            onClick={handleClick}
        >
            <div className="lib-book-card-cover">
                {selectMode && (
                    <input
                        type="checkbox"
                        className="lib-book-card-checkbox"
                        checked={isSelected}
                        readOnly
                        onClick={(e) => { e.stopPropagation(); onSelect(e) }}
                    />
                )}
                {coverUrl ? (
                    <img src={coverUrl} alt={book.title} loading="lazy" />
                ) : (
                    <div className="lib-book-card-placeholder">
                        {book.mediaType === 'pair' ? '🔗' : book.mediaType === 'audiobook' ? '🎧' : '📚'}
                    </div>
                )}
                <TypeBadge mediaType={book.mediaType} />
                {/* Three-dot menu */}
                {canEdit && !selectMode && (
                    <div className="lib-book-card-menu" ref={menuRef} onClick={e => e.stopPropagation()}>
                        <button
                            className="lib-book-card-menu-btn"
                            onClick={() => setMenuOpen(o => !o)}
                            title="More options"
                        >
                            <svg viewBox="0 0 24 24" fill="currentColor" width="16" height="16">
                                <circle cx="12" cy="5" r="1.5" /><circle cx="12" cy="12" r="1.5" /><circle cx="12" cy="19" r="1.5" />
                            </svg>
                        </button>
                        {menuOpen && (
                            <div className="lib-book-card-dropdown">
                                <button onClick={() => { setMenuOpen(false); onNavigate() }}>View Details</button>
                                <button onClick={() => { setMenuOpen(false); onEdit() }}>Edit Metadata</button>
                                {book.mediaType !== 'pair' && (
                                    <button className="danger" onClick={() => { setMenuOpen(false); onDelete() }}>Delete</button>
                                )}
                            </div>
                        )}
                    </div>
                )}
            </div>
            <div className="lib-book-card-title" title={book.title}>{book.title}</div>
            {book.author && <div className="lib-book-card-author">{book.author}</div>}
            {book.series && (
                <div className="lib-book-card-series">
                    {book.series}{book.series_index ? ` #${book.series_index}` : ''}
                </div>
            )}
        </div>
    )
}

// ---- Book Row (List View) ----

function BookRow({ book, selectMode, isSelected, onSelect, onEdit, onDelete, onNavigate, canEdit }) {
    const coverUrl = coverSrc(book.cover_path)

    const handleClick = (e) => {
        if (selectMode) { onSelect(e); return }
        onNavigate()
    }

    return (
        <div
            className={`lib-book-row${selectMode ? ' select-mode' : ''}${isSelected ? ' selected' : ''}`}
            onClick={handleClick}
        >
            {selectMode && (
                <div onClick={e => e.stopPropagation()}>
                    <input type="checkbox" checked={isSelected} readOnly onClick={(e) => onSelect(e)} style={{ cursor: 'pointer', accentColor: 'var(--accent)' }} />
                </div>
            )}
            <div>
                {coverUrl ? (
                    <img className="lib-book-row-thumb" src={coverUrl} alt={book.title} loading="lazy" />
                ) : (
                    <div className="lib-book-row-thumb-placeholder">
                        {book.mediaType === 'pair' ? '🔗' : book.mediaType === 'audiobook' ? '🎧' : '📚'}
                    </div>
                )}
            </div>
            <div className="lib-book-row-title">
                {book.title}
                <span style={{ marginLeft: 6 }}><TypeBadge mediaType={book.mediaType} /></span>
            </div>
            <div className="lib-book-row-cell">{book.author || '\u2014'}</div>
            <div className="lib-book-row-cell">
                {book.series ? `${book.series}${book.series_index ? ` #${book.series_index}` : ''}` : '\u2014'}
            </div>
            <div className="lib-book-row-cell">
                {book.mediaType !== 'pair' && <span className="badge badge-auto_matched">{book.format}</span>}
            </div>
            <div className="lib-book-row-cell muted">{formatSize(book.file_size)}</div>
            <div className="lib-book-row-cell muted">{new Date(book.uploaded_at).toLocaleDateString()}</div>
            <div className="lib-book-row-actions" onClick={e => e.stopPropagation()}>
                {canEdit && !selectMode && book.mediaType !== 'pair' && (
                    <>
                        <button
                            className="btn btn-sm btn-secondary"
                            onClick={() => onEdit()}
                            title="Edit metadata"
                            style={{ padding: '4px 8px', fontSize: '0.8rem' }}
                        >
                            ✏️
                        </button>
                        <button
                            className="btn btn-sm btn-danger"
                            onClick={() => onDelete()}
                            title="Delete"
                            style={{ padding: '4px 8px', fontSize: '0.8rem' }}
                        >
                            🗑️
                        </button>
                    </>
                )}
            </div>
        </div>
    )
}

// ---- Main Component ----

function LibraryPage({ tab }) {
    const navigate = useNavigate()
    const [searchParams, setSearchParams] = useSearchParams()
    const { hasMinRole } = useAuth()
    const canEdit = hasMinRole('editor')
    const isMobile = useIsMobile()
    const [mobileSearch, setMobileSearch] = useState('')
    const [mobileUploadOpen, setMobileUploadOpen] = useState(false)
    const mobileUploadRef = useRef(null)

    // Data state
    const [ebooks, setEbooks] = useState([])
    const [audiobooks, setAudiobooks] = useState([])
    const [pairs, setPairs] = useState([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState('')
    const [scanResult, setScanResult] = useState(null)

    // Operation flags
    const [scanning, setScanning] = useState(false)
    const [normalizing, setNormalizing] = useState(false)
    const [rescanningAll, setRescanningAll] = useState(false)
    const [uploadingEbook, setUploadingEbook] = useState(false)
    const [uploadingAudiobook, setUploadingAudiobook] = useState(false)

    // View state
    const [viewMode, setViewMode] = useState('grid')
    const [activeFilter, setActiveFilter] = useState('all')
    const [unpairedSubFilter, setUnpairedSubFilter] = useState('all') // 'all' | 'ebooks' | 'audiobooks'
    const [newSubFilter, setNewSubFilter] = useState('all') // 'all' | 'ebooks' | 'audiobooks' | 'pairs'
    const [newPairs, setNewPairs] = useState([])
    const [acknowledging, setAcknowledging] = useState(false)
    const [showMetadataCleanup, setShowMetadataCleanup] = useState(false)
    const [gridPage, setGridPage] = useState(1)

    const GRID_PAGE_SIZE = 50
    const [sortField, setSortField] = useState('title')
    const [sortDir, setSortDir] = useState('asc')
    const [sortOpen, setSortOpen] = useState(false)
    const [searchTerm, setSearchTerm] = useState('')
    const [authorFilter, setAuthorFilter] = useState('')
    const [seriesFilter, setSeriesFilter] = useState('')

    // Edit state
    const [editingBook, setEditingBook] = useState(null)
    const [editingType, setEditingType] = useState(null)

    // Delete state
    const [deleteTarget, setDeleteTarget] = useState(null)
    const [deleteSourceFile, setDeleteSourceFile] = useState(false)
    const [deleting, setDeleting] = useState(false)

    // Verify state
    const [verifying, setVerifying] = useState(false)
    const [verifyReport, setVerifyReport] = useState(null)
    const [selectedOrphanEbooks, setSelectedOrphanEbooks] = useState(new Set())
    const [selectedOrphanAudiobooks, setSelectedOrphanAudiobooks] = useState(new Set())
    const [cleaningUp, setCleaningUp] = useState(false)

    // Select mode & bulk actions
    const [selectMode, setSelectMode] = useState(false)
    const [selectedIds, setSelectedIds] = useState(new Set())
    const lastSelectedIndexRef = useRef(null)
    const [bulkEditOpen, setBulkEditOpen] = useState(false)
    const [bulkEditFields, setBulkEditFields] = useState({ author: '', series: '', series_index: '', publisher: '', published_year: '' })
    const [bulkSaving, setBulkSaving] = useState(false)
    const [bulkMatchOpen, setBulkMatchOpen] = useState(false)
    const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false)
    const [bulkDeleteSourceFile, setBulkDeleteSourceFile] = useState(false)
    const [bulkDeleting, setBulkDeleting] = useState(false)

    // Dropdown refs
    const maintenanceRef = useRef(null)
    const uploadRef = useRef(null)
    const sortRef = useRef(null)
    const [maintenanceOpen, setMaintenanceOpen] = useState(false)
    const [uploadOpen, setUploadOpen] = useState(false)
    const ebookFileRef = useRef(null)
    const audiobookFileRef = useRef(null)

    // Sync search from URL (global search bar owns it — don't clear it)
    // One-time navigation params (author/series from SeriesPage links) are cleared after reading
    useEffect(() => {
        const urlSearch = searchParams.get('search')
        const urlAuthor = searchParams.get('author')
        const urlSeries = searchParams.get('series')
        setSearchTerm(urlSearch || '')
        if (urlAuthor) setAuthorFilter(urlAuthor)
        if (urlSeries) setSeriesFilter(urlSeries)
        if (urlAuthor || urlSeries) {
            setSearchParams(prev => {
                const n = new URLSearchParams(prev)
                n.delete('author')
                n.delete('series')
                return n
            }, { replace: true })
        }
    }, [searchParams]) // eslint-disable-line react-hooks/exhaustive-deps

    // Set initial filter based on tab prop
    useEffect(() => {
        if (tab === 'audiobooks') setActiveFilter('audiobooks')
        else if (tab === 'ebooks') setActiveFilter('ebooks')
    }, [tab])

    // Pair lookup maps
    const pairMaps = useMemo(() => {
        const byEbookId = {}
        const byAudiobookId = {}
        pairs.forEach(p => {
            if (p.ebook?.id) byEbookId[p.ebook.id] = p
            if (p.audiobook?.id) byAudiobookId[p.audiobook.id] = p
        })
        return { byEbookId, byAudiobookId }
    }, [pairs])

    // Annotated individual book lists (with pair info)
    const annotatedEbooks = useMemo(() =>
        ebooks.map(b => ({
            ...b,
            mediaType: 'ebook',
            pair_id: pairMaps.byEbookId[b.id]?.id ?? null,
            pair_status: pairMaps.byEbookId[b.id]?.status ?? null,
            paired_audiobook_id: pairMaps.byEbookId[b.id]?.audiobook?.id ?? null,
        })),
    [ebooks, pairMaps])

    const annotatedAudiobooks = useMemo(() =>
        audiobooks.map(b => ({
            ...b,
            mediaType: 'audiobook',
            pair_id: pairMaps.byAudiobookId[b.id]?.id ?? null,
            pair_status: pairMaps.byAudiobookId[b.id]?.status ?? null,
            paired_ebook_id: pairMaps.byAudiobookId[b.id]?.ebook?.id ?? null,
        })),
    [audiobooks, pairMaps])

    // Merged pair entries (one entry per pair for All/Paired views)
    const pairEntries = useMemo(() => {
        const ebookMap = {}
        const abMap = {}
        ebooks.forEach(b => { ebookMap[b.id] = b })
        audiobooks.forEach(b => { abMap[b.id] = b })

        return pairs.map(p => {
            const eb = p.ebook?.id ? ebookMap[p.ebook.id] : null
            const ab = p.audiobook?.id ? abMap[p.audiobook.id] : null
            if (!eb && !ab) return null
            // Prefer ebook as primary source for metadata/cover
            const primary = eb || ab
            return {
                ...primary,
                mediaType: 'pair',
                cover_path: eb?.cover_path || ab?.cover_path,
                title: eb?.title || ab?.title,
                author: eb?.author || ab?.author,
                series: eb?.series || ab?.series,
                series_index: eb?.series_index ?? ab?.series_index,
                uploaded_at: eb?.uploaded_at || ab?.uploaded_at,
                pair_id: p.id,
                pair_status: p.status,
                ebook_id: eb?.id ?? null,
                audiobook_id: ab?.id ?? null,
            }
        }).filter(Boolean)
    }, [pairs, ebooks, audiobooks])

    // Filtered + sorted display list
    const filteredBooks = useMemo(() => {
        let list

        if (activeFilter === 'all') {
            // Pairs as one entry + unpaired individual items
            const pairedEbookIds = new Set(annotatedEbooks.filter(b => b.pair_id).map(b => b.id))
            const pairedAudiobookIds = new Set(annotatedAudiobooks.filter(b => b.pair_id).map(b => b.id))
            const unpairedEbooks = annotatedEbooks.filter(b => !pairedEbookIds.has(b.id))
            const unpairedAudiobooks = annotatedAudiobooks.filter(b => !pairedAudiobookIds.has(b.id))
            list = [...pairEntries, ...unpairedEbooks, ...unpairedAudiobooks]
        } else if (activeFilter === 'ebooks') {
            list = annotatedEbooks
        } else if (activeFilter === 'audiobooks') {
            list = annotatedAudiobooks
        } else if (activeFilter === 'paired') {
            list = pairEntries
        } else if (activeFilter === 'unpaired') {
            const unpEl = annotatedEbooks.filter(b => !b.pair_id)
            const unpAb = annotatedAudiobooks.filter(b => !b.pair_id)
            if (unpairedSubFilter === 'ebooks') list = unpEl
            else if (unpairedSubFilter === 'audiobooks') list = unpAb
            else list = [...unpEl, ...unpAb]
        } else if (activeFilter === 'new') {
            const newEbooks = annotatedEbooks.filter(b => !b.acknowledged)
            const newAudiobooks = annotatedAudiobooks.filter(b => !b.acknowledged)
            if (newSubFilter === 'ebooks') list = newEbooks
            else if (newSubFilter === 'audiobooks') list = newAudiobooks
            else if (newSubFilter === 'pairs') {
                // Show new pairs as pairEntries filtered to new pair IDs
                const newPairIds = new Set(newPairs.map(p => p.id))
                list = pairEntries.filter(p => newPairIds.has(p.pair_id))
            } else list = [...newEbooks, ...newAudiobooks]
        } else {
            list = [...annotatedEbooks, ...annotatedAudiobooks]
        }

        // Text search (desktop uses URL param, mobile uses local state)
        const effectiveSearch = searchTerm || mobileSearch
        if (effectiveSearch) {
            const term = effectiveSearch.toLowerCase()
            list = list.filter(b =>
                b.title?.toLowerCase().includes(term) ||
                b.author?.toLowerCase().includes(term) ||
                b.series?.toLowerCase().includes(term)
            )
        }

        // Explicit author / series filters (from SeriesPage navigation)
        if (authorFilter) list = list.filter(b => b.author === authorFilter)
        if (seriesFilter) list = list.filter(b => b.series === seriesFilter)

        return [...list].sort(sortComparator(`${sortField}-${sortDir}`))
    }, [annotatedEbooks, annotatedAudiobooks, pairEntries, searchTerm, mobileSearch, activeFilter, unpairedSubFilter, newSubFilter, newPairs, sortField, sortDir, authorFilter, seriesFilter])

    // Stats
    const stats = useMemo(() => {
        const newEbooksCount = ebooks.filter(b => !b.acknowledged).length
        const newAudiobooksCount = audiobooks.filter(b => !b.acknowledged).length
        const newCount = newEbooksCount + newAudiobooksCount + newPairs.length
        return {
            totalEbooks: ebooks.length,
            totalAudiobooks: audiobooks.length,
            pairCount: pairs.length,
            unpaired: ebooks.filter(b => !pairMaps.byEbookId[b.id]).length + audiobooks.filter(b => !pairMaps.byAudiobookId[b.id]).length,
            newCount,
            newEbooksCount,
            newAudiobooksCount,
            newPairsCount: newPairs.length,
            displayTotal: filteredBooks.length,
        }
    }, [ebooks, audiobooks, pairs, pairMaps, filteredBooks, newPairs])

    // Base list for filter pill options — scoped to current activeFilter (no search/author/series applied)
    const baseListForOptions = useMemo(() => {
        if (activeFilter === 'ebooks')    return annotatedEbooks
        if (activeFilter === 'audiobooks') return annotatedAudiobooks
        if (activeFilter === 'paired')    return pairEntries
        if (activeFilter === 'unpaired') {
            const unpEl = annotatedEbooks.filter(b => !b.pair_id)
            const unpAb = annotatedAudiobooks.filter(b => !b.pair_id)
            if (unpairedSubFilter === 'ebooks') return unpEl
            if (unpairedSubFilter === 'audiobooks') return unpAb
            return [...unpEl, ...unpAb]
        }
        if (activeFilter === 'new') {
            const newEb = annotatedEbooks.filter(b => !b.acknowledged)
            const newAb = annotatedAudiobooks.filter(b => !b.acknowledged)
            if (newSubFilter === 'ebooks') return newEb
            if (newSubFilter === 'audiobooks') return newAb
            if (newSubFilter === 'pairs') {
                const ids = new Set(newPairs.map(p => p.id))
                return pairEntries.filter(p => ids.has(p.pair_id))
            }
            return [...newEb, ...newAb]
        }
        // 'all'
        const pairedEbookIds = new Set(annotatedEbooks.filter(b => b.pair_id).map(b => b.id))
        const pairedAudiobookIds = new Set(annotatedAudiobooks.filter(b => b.pair_id).map(b => b.id))
        return [
            ...pairEntries,
            ...annotatedEbooks.filter(b => !pairedEbookIds.has(b.id)),
            ...annotatedAudiobooks.filter(b => !pairedAudiobookIds.has(b.id)),
        ]
    }, [activeFilter, annotatedEbooks, annotatedAudiobooks, pairEntries, unpairedSubFilter, newSubFilter, newPairs])

    const allAuthors = useMemo(() => {
        const set = new Set(baseListForOptions.map(b => b.author).filter(Boolean))
        return [...set].sort()
    }, [baseListForOptions])

    const allSeriesNames = useMemo(() => {
        const set = new Set(baseListForOptions.map(b => b.series).filter(Boolean))
        return [...set].sort()
    }, [baseListForOptions])

    // Data loading
    const loadData = useCallback(async () => {
        try {
            const [e, a, p, np] = await Promise.all([getEbooks(), getAudiobooks(), getPairs(), getNewPairs()])
            setEbooks(e)
            setAudiobooks(a)
            setPairs(p)
            setNewPairs(np)
        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => { loadData() }, [loadData])

    // Reset grid pagination whenever the filtered list changes
    useEffect(() => { setGridPage(1) }, [filteredBooks])

    // Close dropdowns on outside click
    useEffect(() => {
        const handler = (e) => {
            if (maintenanceRef.current && !maintenanceRef.current.contains(e.target)) setMaintenanceOpen(false)
            if (uploadRef.current && !uploadRef.current.contains(e.target)) setUploadOpen(false)
            if (sortRef.current && !sortRef.current.contains(e.target)) setSortOpen(false)
            if (mobileUploadRef.current && !mobileUploadRef.current.contains(e.target)) setMobileUploadOpen(false)
        }
        document.addEventListener('mousedown', handler)
        return () => document.removeEventListener('mousedown', handler)
    }, [])

    // ---- Selection helpers ----

    const bookKey = (b) => {
        if (b.mediaType === 'pair') return `pair-${b.pair_id}`
        return `${b.mediaType}-${b.id}`
    }

    const handleSelect = useCallback((book, idx, shiftKey) => {
        const key = bookKey(book)
        if (shiftKey && lastSelectedIndexRef.current !== null) {
            const start = Math.min(lastSelectedIndexRef.current, idx)
            const end = Math.max(lastSelectedIndexRef.current, idx)
            const rangeKeys = filteredBooks.slice(start, end + 1).map(b => bookKey(b))
            setSelectedIds(prev => {
                const next = new Set(prev)
                rangeKeys.forEach(k => next.add(k))
                return next
            })
        } else {
            setSelectedIds(prev => {
                const next = new Set(prev)
                next.has(key) ? next.delete(key) : next.add(key)
                return next
            })
            lastSelectedIndexRef.current = idx
        }
    }, [filteredBooks])

    const toggleSelectAll = () => {
        const allSelected = filteredBooks.length > 0 && filteredBooks.every(b => selectedIds.has(bookKey(b)))
        if (allSelected) {
            setSelectedIds(new Set())
        } else {
            setSelectedIds(new Set(filteredBooks.map(b => bookKey(b))))
            lastSelectedIndexRef.current = null
        }
    }

    const exitSelectMode = () => {
        setSelectMode(false)
        setSelectedIds(new Set())
        lastSelectedIndexRef.current = null
    }

    // Parse selected IDs back to {mediaType, id}
    const parseSelectedIds = () => {
        return [...selectedIds].flatMap(key => {
            if (key.startsWith('pair-')) {
                const pairId = parseInt(key.slice(5))
                const pair = pairs.find(p => p.id === pairId)
                if (!pair) return []
                const results = []
                if (pair.ebook?.id) results.push({ mediaType: 'ebook', id: pair.ebook.id })
                if (pair.audiobook?.id) results.push({ mediaType: 'audiobook', id: pair.audiobook.id })
                return results
            }
            const idx = key.lastIndexOf('-')
            return [{ mediaType: key.slice(0, idx), id: parseInt(key.slice(idx + 1)) }]
        })
    }

    // ---- Bulk operations ----

    const handleBulkEdit = async () => {
        const patch = {}
        if (bulkEditFields.author.trim()) patch.author = bulkEditFields.author.trim()
        if (bulkEditFields.series.trim()) patch.series = bulkEditFields.series.trim()
        if (bulkEditFields.series_index.trim()) patch.series_index = parseFloat(bulkEditFields.series_index) || null
        if (bulkEditFields.publisher.trim()) patch.publisher = bulkEditFields.publisher.trim()
        if (bulkEditFields.published_year.trim()) patch.published_year = parseInt(bulkEditFields.published_year) || null
        if (Object.keys(patch).length === 0) return

        setBulkSaving(true)
        try {
            const selected = parseSelectedIds()
            await Promise.all(selected.map(s => {
                const fn = s.mediaType === 'ebook' ? updateEbookMetadata : updateAudiobookMetadata
                return fn(s.id, patch)
            }))
            setEbooks(prev => prev.map(b => selected.some(s => s.mediaType === 'ebook' && s.id === b.id) ? { ...b, ...patch } : b))
            setAudiobooks(prev => prev.map(b => selected.some(s => s.mediaType === 'audiobook' && s.id === b.id) ? { ...b, ...patch } : b))
            setBulkEditOpen(false)
            setBulkEditFields({ author: '', series: '', series_index: '', publisher: '', published_year: '' })
            exitSelectMode()
        } catch (err) {
            alert('Bulk edit failed: ' + err.message)
        } finally {
            setBulkSaving(false)
        }
    }

    const executeBulkDelete = async () => {
        setBulkDeleting(true)
        try {
            const selected = parseSelectedIds()
            await Promise.all(selected.map(s => {
                const fn = s.mediaType === 'ebook' ? deleteEbook : deleteAudiobook
                return fn(s.id, bulkDeleteSourceFile)
            }))
            await loadData()
            setBulkDeleteOpen(false)
            exitSelectMode()
        } catch (err) {
            alert('Delete failed: ' + err.message)
        } finally {
            setBulkDeleting(false)
        }
    }

    // ---- Library operations ----

    const handleScan = async () => {
        setScanning(true); setScanResult(null); setError('')
        try { const r = await scanLibrary(); setScanResult(r); await loadData() }
        catch (err) { setError(err.message) }
        finally { setScanning(false) }
    }

    const handleNormalize = async () => {
        setNormalizing(true); setScanResult(null); setError('')
        try { const r = await normalizeLibrary(); setScanResult(r); await loadData() }
        catch (err) { setError(err.message) }
        finally { setNormalizing(false) }
    }

    const handleRescanAll = async () => {
        if (!window.confirm("WARNING: This will force a complete rescan of EVERY file in your library, overwriting all current database metadata with whatever tags are physically embedded inside the files. This cannot be undone!\n\nAre you sure?")) return
        setRescanningAll(true); setScanResult(null); setError('')
        try { const r = await rescanAllLibrary(); setScanResult(r); await loadData() }
        catch (err) { setError(err.message) }
        finally { setRescanningAll(false) }
    }

    const handleEbookUpload = async (e) => {
        const file = e.target.files[0]; if (!file) return
        setUploadingEbook(true); setError('')
        try { await uploadEbook(file); await loadData() }
        catch (err) { setError(err.message) }
        finally { setUploadingEbook(false); ebookFileRef.current.value = '' }
    }

    const handleAudiobookUpload = async (e) => {
        const file = e.target.files[0]; if (!file) return
        setUploadingAudiobook(true); setError('')
        try { await uploadAudiobook(file); await loadData() }
        catch (err) { setError(err.message) }
        finally { setUploadingAudiobook(false); audiobookFileRef.current.value = '' }
    }

    const handleSaveMetadata = async (bookId, meta) => {
        try {
            if (editingType === 'ebook') {
                await updateEbookMetadata(bookId, meta)
                setEbooks(prev => prev.map(b => b.id === bookId ? { ...b, ...meta } : b))
            } else {
                await updateAudiobookMetadata(bookId, meta)
                setAudiobooks(prev => prev.map(b => b.id === bookId ? { ...b, ...meta } : b))
            }
            setEditingBook(null); setEditingType(null)
        } catch (err) {
            alert('Failed to update metadata: ' + err.message)
        }
    }

    const openEdit = (book) => {
        if (book.mediaType === 'pair') {
            // Edit the ebook component of the pair
            const eb = ebooks.find(b => b.id === book.ebook_id)
            if (eb) { setEditingBook({ ...eb }); setEditingType('ebook') }
        } else {
            setEditingBook({ ...book })
            setEditingType(book.mediaType)
        }
    }

    const openDeleteModal = (book) => {
        setDeleteTarget({ id: book.id, title: book.title, author: book.author, type: book.mediaType })
        setDeleteSourceFile(false)
    }

    const handleDelete = async () => {
        if (!deleteTarget) return
        setDeleting(true)
        try {
            if (deleteTarget.type === 'ebook') {
                await deleteEbook(deleteTarget.id, deleteSourceFile)
                setEbooks(prev => prev.filter(b => b.id !== deleteTarget.id))
            } else {
                await deleteAudiobook(deleteTarget.id, deleteSourceFile)
                setAudiobooks(prev => prev.filter(b => b.id !== deleteTarget.id))
            }
            await loadData()
            setDeleteTarget(null)
        } catch (err) {
            alert('Failed to delete: ' + err.message)
        } finally {
            setDeleting(false)
        }
    }

    // ---- Acknowledge new items ----

    const handleAcknowledgeAll = async () => {
        setAcknowledging(true)
        try {
            if (newSubFilter === 'pairs') {
                await acknowledgeNewPairs(newPairs.map(p => p.id))
            } else if (newSubFilter === 'ebooks') {
                const ids = filteredBooks.map(b => b.id)
                await acknowledgeNewItems(ids, [])
            } else if (newSubFilter === 'audiobooks') {
                const ids = filteredBooks.map(b => b.id)
                await acknowledgeNewItems([], ids)
            } else {
                // all: ebooks + audiobooks + pairs
                const ebookIds = ebooks.filter(b => !b.acknowledged).map(b => b.id)
                const audioIds = audiobooks.filter(b => !b.acknowledged).map(b => b.id)
                const pairIds = newPairs.map(p => p.id)
                await Promise.all([
                    acknowledgeNewItems(ebookIds, audioIds),
                    ...(pairIds.length ? [acknowledgeNewPairs(pairIds)] : []),
                ])
            }
            await loadData()
        } catch (err) {
            alert('Failed to acknowledge: ' + err.message)
        } finally {
            setAcknowledging(false)
        }
    }

    // ---- Verify handlers ----

    const handleVerify = async () => {
        setVerifying(true); setVerifyReport(null); setError('')
        try {
            const report = await verifyFiles()
            setVerifyReport(report)
            setSelectedOrphanEbooks(new Set())
            setSelectedOrphanAudiobooks(new Set())
        } catch (err) { setError(err.message) }
        finally { setVerifying(false) }
    }

    const toggleOrphanEbook = (id) => setSelectedOrphanEbooks(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n })
    const toggleOrphanAudiobook = (id) => setSelectedOrphanAudiobooks(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n })
    const selectAllOrphans = () => { if (!verifyReport) return; setSelectedOrphanEbooks(new Set(verifyReport.orphaned_ebooks.map(e => e.id))); setSelectedOrphanAudiobooks(new Set(verifyReport.orphaned_audiobooks.map(a => a.id))) }
    const deselectAllOrphans = () => { setSelectedOrphanEbooks(new Set()); setSelectedOrphanAudiobooks(new Set()) }

    const handleCleanup = async () => {
        if (selectedOrphanEbooks.size === 0 && selectedOrphanAudiobooks.size === 0) return
        setCleaningUp(true)
        try {
            const result = await cleanupOrphans([...selectedOrphanEbooks], [...selectedOrphanAudiobooks])
            setEbooks(prev => prev.filter(b => !selectedOrphanEbooks.has(b.id)))
            setAudiobooks(prev => prev.filter(b => !selectedOrphanAudiobooks.has(b.id)))
            setVerifyReport(prev => ({
                orphaned_ebooks: prev.orphaned_ebooks.filter(e => !selectedOrphanEbooks.has(e.id)),
                orphaned_audiobooks: prev.orphaned_audiobooks.filter(a => !selectedOrphanAudiobooks.has(a.id)),
            }))
            setSelectedOrphanEbooks(new Set()); setSelectedOrphanAudiobooks(new Set())
            setScanResult({ message: result.message })
        } catch (err) { alert('Cleanup failed: ' + err.message) }
        finally { setCleaningUp(false) }
    }

    // ---- Bulk match helper ----

    const selectedBooksForMatch = useMemo(() => {
        const parsed = parseSelectedIds()
        const types = new Set(parsed.map(s => s.mediaType))
        if (types.size !== 1) return null
        const type = [...types][0]
        const source = type === 'ebook' ? ebooks : audiobooks
        return {
            books: source.filter(b => parsed.some(s => s.id === b.id)),
            bookType: type,
        }
    }, [selectedIds, ebooks, audiobooks, pairs])

    // ---- Render ----

    if (loading) {
        return <div className="loading-page"><div className="spinner"></div> Loading library...</div>
    }

    const filterPills = [
        { key: 'all', label: 'All', count: pairEntries.length + ebooks.filter(b => !pairMaps.byEbookId[b.id]).length + audiobooks.filter(b => !pairMaps.byAudiobookId[b.id]).length },
        { key: 'ebooks', label: 'Ebooks', count: ebooks.length },
        { key: 'audiobooks', label: 'Audiobooks', count: audiobooks.length },
        { key: 'paired', label: 'Paired', count: pairs.length },
        { key: 'unpaired', label: 'Unpaired', count: stats.unpaired },
        ...(stats.newCount > 0 ? [{ key: 'new', label: 'New', count: stats.newCount }] : []),
    ]

    return (
        <div className="library-page">
            {/* Alerts */}
            <div className="library-alerts">
                {error && <div className="alert alert-error">{error}</div>}
                {scanResult && <div className="alert alert-success">{scanResult.message}</div>}
            </div>

            {/* Toolbar */}
            <div className="library-toolbar">
                <div className="library-toolbar-left">
                    {/* Filter Pills */}
                    <div className="library-filter-pills">
                        {filterPills.map(p => (
                            <button
                                key={p.key}
                                className={`library-filter-pill${activeFilter === p.key ? ' active' : ''}`}
                                onClick={() => { setActiveFilter(p.key); setUnpairedSubFilter('all'); setNewSubFilter('all') }}
                            >
                                {p.label} ({p.count})
                            </button>
                        ))}
                    </div>

                    {/* Author / Series filter pills */}
                    <FilterPill
                        label="Author"
                        value={authorFilter}
                        options={allAuthors}
                        onChange={setAuthorFilter}
                    />
                    <FilterPill
                        label="Series"
                        value={seriesFilter}
                        options={allSeriesNames}
                        onChange={setSeriesFilter}
                    />

                    {/* Active search chip (cleared via global search bar or ✕) */}
                    {searchTerm && (
                        <div className="library-search-active">
                            <span>Search: <strong>{searchTerm}</strong></span>
                            <button className="library-search-clear" onClick={() => setSearchParams(prev => { const n = new URLSearchParams(prev); n.delete('search'); return n }, { replace: true })}>✕</button>
                        </div>
                    )}
                </div>

                <div className="library-toolbar-right">
                    {/* Sort pill */}
                    <LibrarySortPill
                        sortField={sortField}
                        setSortField={setSortField}
                        sortDir={sortDir}
                        setSortDir={setSortDir}
                        sortOpen={sortOpen}
                        setSortOpen={setSortOpen}
                        sortRef={sortRef}
                    />

                    {/* View Toggle */}
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

                    {/* Upload dropdown */}
                    {canEdit && (
                        <div className="library-action-dropdown" ref={uploadRef}>
                            <button
                                className="btn btn-primary"
                                onClick={() => setUploadOpen(o => !o)}
                                disabled={selectMode}
                                style={{ fontSize: '0.85rem', padding: '6px 14px' }}
                            >
                                + Upload
                            </button>
                            {uploadOpen && (
                                <div className="library-dropdown-menu">
                                    <button className="library-dropdown-item" onClick={() => { setUploadOpen(false); ebookFileRef.current?.click() }} disabled={uploadingEbook}>
                                        {uploadingEbook ? 'Uploading...' : 'Upload Ebook'}
                                    </button>
                                    <button className="library-dropdown-item" onClick={() => { setUploadOpen(false); audiobookFileRef.current?.click() }} disabled={uploadingAudiobook}>
                                        {uploadingAudiobook ? 'Uploading...' : 'Upload Audiobook'}
                                    </button>
                                </div>
                            )}
                        </div>
                    )}

                    {/* Maintenance dropdown */}
                    {canEdit && (
                        <div className="library-action-dropdown" ref={maintenanceRef}>
                            <button
                                className="btn btn-secondary"
                                onClick={() => setMaintenanceOpen(o => !o)}
                                disabled={scanning || normalizing || rescanningAll || selectMode}
                                style={{ fontSize: '0.85rem', padding: '6px 14px' }}
                            >
                                Maintenance
                            </button>
                            {maintenanceOpen && (
                                <div className="library-dropdown-menu">
                                    <button className="library-dropdown-item" onClick={() => { setMaintenanceOpen(false); handleScan() }} disabled={scanning}>
                                        {scanning ? 'Scanning...' : 'Scan Directories'}
                                    </button>
                                    <button className="library-dropdown-item" onClick={() => { setMaintenanceOpen(false); handleNormalize() }} disabled={normalizing}>
                                        {normalizing ? 'Normalizing...' : 'Normalize Metadata'}
                                    </button>
                                    <button className="library-dropdown-item danger" onClick={() => { setMaintenanceOpen(false); handleRescanAll() }} disabled={rescanningAll}>
                                        {rescanningAll ? 'Rescanning...' : 'Force Rescan All'}
                                    </button>
                                    <button className="library-dropdown-item" onClick={() => { setMaintenanceOpen(false); handleVerify() }} disabled={verifying}>
                                        {verifying ? 'Verifying...' : 'Verify Files'}
                                    </button>
                                    <hr style={{ margin: '4px 0', border: 'none', borderTop: '1px solid var(--border)' }} />
                                    <button className="library-dropdown-item" onClick={() => { setMaintenanceOpen(false); navigate('/pairs/unpaired') }}>
                                        Pair Ebook + Audiobook
                                    </button>
                                </div>
                            )}
                        </div>
                    )}

                    {/* Select mode toggle */}
                    {canEdit && (
                        <button
                            className={`btn ${selectMode ? 'btn-primary' : 'btn-secondary'}`}
                            onClick={() => selectMode ? exitSelectMode() : setSelectMode(true)}
                            style={{ fontSize: '0.85rem', padding: '6px 14px' }}
                        >
                            {selectMode ? `Cancel (${selectedIds.size})` : 'Select'}
                        </button>
                    )}
                </div>
            </div>

            {/* Sub-filter row — shown when Unpaired or New is active */}
            {activeFilter === 'unpaired' && (
                <div className="library-sub-filter-row">
                    {[
                        { key: 'all', label: 'All' },
                        { key: 'ebooks', label: 'Ebooks' },
                        { key: 'audiobooks', label: 'Audiobooks' },
                    ].map(s => (
                        <button
                            key={s.key}
                            className={`library-filter-pill library-filter-pill-sub${unpairedSubFilter === s.key ? ' active' : ''}`}
                            onClick={() => setUnpairedSubFilter(s.key)}
                        >
                            {s.label}
                        </button>
                    ))}
                </div>
            )}
            {activeFilter === 'new' && (
                <div className="library-sub-filter-row">
                    {[
                        { key: 'all', label: `All (${stats.newEbooksCount + stats.newAudiobooksCount + stats.newPairsCount})` },
                        { key: 'ebooks', label: `Ebooks (${stats.newEbooksCount})` },
                        { key: 'audiobooks', label: `Audiobooks (${stats.newAudiobooksCount})` },
                        { key: 'pairs', label: `Pairs (${stats.newPairsCount})` },
                    ].map(s => (
                        <button
                            key={s.key}
                            className={`library-filter-pill library-filter-pill-sub${newSubFilter === s.key ? ' active' : ''}`}
                            onClick={() => setNewSubFilter(s.key)}
                        >
                            {s.label}
                        </button>
                    ))}
                </div>
            )}

            {/* Mobile search input */}
            {isMobile && (
                <div className="library-mobile-search">
                    <span className="search-icon material-symbols-outlined" style={{ fontSize: 20 }}>search</span>
                    <input
                        type="text"
                        placeholder="Search books..."
                        value={mobileSearch}
                        onChange={e => setMobileSearch(e.target.value)}
                    />
                </div>
            )}

            {/* Mobile view controls */}
            {isMobile && (
                <div className="library-mobile-controls">
                    <span className="showing-count">Showing {filteredBooks.length} items</span>
                    <div className="control-buttons">
                        <button
                            className={`control-btn${viewMode === 'grid' ? ' active' : ''}`}
                            onClick={() => setViewMode('grid')}
                        >
                            <span className="material-symbols-outlined" style={{ fontSize: 20 }}>grid_view</span>
                        </button>
                        <button
                            className={`control-btn${viewMode === 'list' ? ' active' : ''}`}
                            onClick={() => setViewMode('list')}
                        >
                            <span className="material-symbols-outlined" style={{ fontSize: 20 }}>view_list</span>
                        </button>
                        <div className="control-divider" />
                        <button
                            className="control-btn"
                            onClick={() => setSortOpen(o => !o)}
                        >
                            <span className="material-symbols-outlined" style={{ fontSize: 20 }}>sort</span>
                        </button>
                    </div>
                </div>
            )}

            {/* Mobile sort dropdown (when opened from mobile controls) */}
            {isMobile && sortOpen && (
                <div style={{ position: 'relative', marginBottom: 16 }} ref={sortRef}>
                    <div className="series-sort-dropdown" style={{ position: 'relative', top: 0, width: '100%' }}>
                        {Object.entries(LIBRARY_SORT_LABELS).map(([key, label]) => (
                            <button
                                key={key}
                                className={`series-sort-option${sortField === key ? ' active' : ''}`}
                                onClick={() => { setSortField(key); setSortOpen(false) }}
                            >
                                {label}
                                {sortField === key && (
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
                                onClick={() => { setSortDir('asc'); setSortOpen(false) }}
                            >↑ Ascending</button>
                            <button
                                className={`series-sort-dir-btn${sortDir === 'desc' ? ' active' : ''}`}
                                onClick={() => { setSortDir('desc'); setSortOpen(false) }}
                            >↓ Descending</button>
                        </div>
                    </div>
                </div>
            )}

            {/* Hidden file inputs */}
            <input ref={ebookFileRef} type="file" accept=".epub,.pdf,.mobi" hidden onChange={handleEbookUpload} />
            <input ref={audiobookFileRef} type="file" accept=".mp3,.m4a,.m4b,.flac,.ogg,.wav" hidden onChange={handleAudiobookUpload} />

            {/* Verify Report */}
            {verifyReport && (
                <div className="card library-verify-report">
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                        <h3 style={{ margin: 0 }}>File Verification Report</h3>
                        <button className="btn btn-secondary" onClick={() => setVerifyReport(null)} style={{ padding: '4px 12px' }}>Close</button>
                    </div>
                    {verifyReport.orphaned_ebooks.length === 0 && verifyReport.orphaned_audiobooks.length === 0 ? (
                        <div className="alert alert-success">All files verified - no orphaned entries found!</div>
                    ) : (
                        <>
                            <div className="alert alert-error" style={{ marginBottom: '16px' }}>
                                Found {verifyReport.orphaned_ebooks.length} orphaned ebook(s) and {verifyReport.orphaned_audiobooks.length} orphaned audiobook(s) with missing source files.
                            </div>
                            <div style={{ display: 'flex', gap: '8px', marginBottom: '16px' }}>
                                <button className="btn btn-secondary" onClick={selectAllOrphans} style={{ padding: '6px 12px', fontSize: '0.85rem' }}>Select All</button>
                                <button className="btn btn-secondary" onClick={deselectAllOrphans} style={{ padding: '6px 12px', fontSize: '0.85rem' }}>Deselect All</button>
                                <button
                                    className="btn btn-danger"
                                    onClick={handleCleanup}
                                    disabled={cleaningUp || (selectedOrphanEbooks.size === 0 && selectedOrphanAudiobooks.size === 0)}
                                    style={{ padding: '6px 12px', fontSize: '0.85rem' }}
                                >
                                    {cleaningUp ? 'Cleaning...' : `Delete Selected (${selectedOrphanEbooks.size + selectedOrphanAudiobooks.size})`}
                                </button>
                            </div>
                            {verifyReport.orphaned_ebooks.length > 0 && (
                                <>
                                    <h4 style={{ margin: '12px 0 8px' }}>Orphaned EBooks</h4>
                                    <div className="table-wrapper">
                                        <table>
                                            <thead><tr><th style={{ width: '40px' }}></th><th>Title</th><th>Author</th><th>File Path</th></tr></thead>
                                            <tbody>
                                                {verifyReport.orphaned_ebooks.map(e => (
                                                    <tr key={e.id}>
                                                        <td><input type="checkbox" checked={selectedOrphanEbooks.has(e.id)} onChange={() => toggleOrphanEbook(e.id)} /></td>
                                                        <td style={{ fontWeight: 500 }}>{e.title}</td>
                                                        <td style={{ color: 'var(--text-secondary)' }}>{e.author || '\u2014'}</td>
                                                        <td style={{ color: 'var(--text-muted)', fontSize: '0.85rem', wordBreak: 'break-all' }}>{e.file_path}</td>
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                    </div>
                                </>
                            )}
                            {verifyReport.orphaned_audiobooks.length > 0 && (
                                <>
                                    <h4 style={{ margin: '12px 0 8px' }}>Orphaned Audiobooks</h4>
                                    <div className="table-wrapper">
                                        <table>
                                            <thead><tr><th style={{ width: '40px' }}></th><th>Title</th><th>Author</th><th>File Path</th></tr></thead>
                                            <tbody>
                                                {verifyReport.orphaned_audiobooks.map(a => (
                                                    <tr key={a.id}>
                                                        <td><input type="checkbox" checked={selectedOrphanAudiobooks.has(a.id)} onChange={() => toggleOrphanAudiobook(a.id)} /></td>
                                                        <td style={{ fontWeight: 500 }}>{a.title}</td>
                                                        <td style={{ color: 'var(--text-secondary)' }}>{a.author || '\u2014'}</td>
                                                        <td style={{ color: 'var(--text-muted)', fontSize: '0.85rem', wordBreak: 'break-all' }}>{a.file_path}</td>
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                    </div>
                                </>
                            )}
                        </>
                    )}
                </div>
            )}

            {/* Stats bar */}
            <div className="library-stats">
                <span className="library-stat"><strong>{ebooks.length}</strong> Ebooks</span>
                <span className="library-stat"><strong>{audiobooks.length}</strong> Audiobooks</span>
                <span className="library-stat"><strong>{pairs.length}</strong> Paired</span>
                <span className="library-stat">Showing <strong>{filteredBooks.length}</strong></span>
            </div>

            {/* Book display */}
            {filteredBooks.length === 0 ? (
                <div className="library-empty">
                    <div className="library-empty-icon">
                        {ebooks.length + audiobooks.length === 0 ? '📚' : '🔍'}
                    </div>
                    <h3>{ebooks.length + audiobooks.length === 0 ? 'No books yet' : 'No books match your filters'}</h3>
                    <p>{ebooks.length + audiobooks.length === 0
                        ? 'Scan your directories or upload books to get started.'
                        : 'Try adjusting your search or filters.'
                    }</p>
                </div>
            ) : (viewMode === 'grid' || isMobile) ? (
                /* ---- Grid View ---- */
                <>
                    <div className="library-grid">
                        {filteredBooks.slice(0, gridPage * GRID_PAGE_SIZE).map((book, idx) => (
                            <BookCard
                                key={bookKey(book)}
                                book={book}
                                selectMode={selectMode}
                                isSelected={selectedIds.has(bookKey(book))}
                                onSelect={(e) => handleSelect(book, idx, e?.shiftKey)}
                                onEdit={() => openEdit(book)}
                                onDelete={() => openDeleteModal(book)}
                                onNavigate={() => navigate(
                                    book.mediaType === 'pair'
                                        ? `/book/ebook/${book.ebook_id}`
                                        : `/book/${book.mediaType}/${book.id}`
                                )}
                                canEdit={canEdit}
                            />
                        ))}
                    </div>
                    {filteredBooks.length > gridPage * GRID_PAGE_SIZE && (
                        <div style={{ textAlign: 'center', marginTop: '24px' }}>
                            <button
                                className="btn btn-secondary"
                                onClick={() => setGridPage(p => p + 1)}
                                style={{ padding: '8px 32px' }}
                            >
                                Show more ({filteredBooks.length - gridPage * GRID_PAGE_SIZE} remaining)
                            </button>
                        </div>
                    )}
                </>
            ) : (
                /* ---- List View ---- */
                <div className="library-list">
                    <div className={`lib-list-header${selectMode ? ' select-mode' : ''}`}>
                        {selectMode && (
                            <div>
                                <input type="checkbox" checked={filteredBooks.length > 0 && filteredBooks.every(b => selectedIds.has(bookKey(b)))} onChange={toggleSelectAll} style={{ cursor: 'pointer', accentColor: 'var(--accent)' }} />
                            </div>
                        )}
                        <div></div>
                        <div>Title</div>
                        <div>Author</div>
                        <div>Series</div>
                        <div>Format</div>
                        <div>Size</div>
                        <div>Added</div>
                        <div></div>
                    </div>
                    {filteredBooks.map((book, idx) => (
                        <BookRow
                            key={bookKey(book)}
                            book={book}
                            selectMode={selectMode}
                            isSelected={selectedIds.has(bookKey(book))}
                            onSelect={(e) => handleSelect(book, idx, e?.shiftKey)}
                            onEdit={() => openEdit(book)}
                            onDelete={() => openDeleteModal(book)}
                            onNavigate={() => navigate(
                                book.mediaType === 'pair'
                                    ? `/book/ebook/${book.ebook_id}`
                                    : `/book/${book.mediaType}/${book.id}`
                            )}
                            canEdit={canEdit}
                        />
                    ))}
                </div>
            )}

            {/* ---- Modals ---- */}

            {/* Delete Confirmation */}
            {deleteTarget && (
                <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 }}>
                    <div className="card" style={{ padding: '24px', maxWidth: '480px', width: '90%' }}>
                        <h3 style={{ marginTop: 0 }}>Confirm Delete</h3>
                        <p>
                            Are you sure you want to delete <strong>{deleteTarget.title}</strong>
                            {deleteTarget.author ? ` by ${deleteTarget.author}` : ''}?
                        </p>
                        <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>
                            This will also remove any associated book pairs, sync maps, and bookmarks.
                        </p>
                        <label style={{ display: 'flex', alignItems: 'center', gap: '8px', margin: '16px 0', cursor: 'pointer' }}>
                            <input type="checkbox" checked={deleteSourceFile} onChange={(e) => setDeleteSourceFile(e.target.checked)} />
                            <span>Also delete the source file from disk</span>
                        </label>
                        {deleteSourceFile && (
                            <div className="alert alert-error" style={{ fontSize: '0.85rem', marginBottom: '16px' }}>
                                This will permanently delete the file from your server's filesystem!
                            </div>
                        )}
                        <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
                            <button className="btn btn-secondary" onClick={() => setDeleteTarget(null)} disabled={deleting}>Cancel</button>
                            <button className="btn btn-danger" onClick={handleDelete} disabled={deleting}>
                                {deleting ? 'Deleting...' : 'Delete'}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* Edit Metadata */}
            {editingBook && (
                <EnhancedMetadataModal
                    book={editingBook}
                    type={editingType}
                    onClose={() => { setEditingBook(null); setEditingType(null) }}
                    onSave={handleSaveMetadata}
                />
            )}

            {/* Floating acknowledge bar — shown when New filter is active */}
            {activeFilter === 'new' && filteredBooks.length > 0 && !selectMode && (
                <div style={{
                    position: 'fixed', bottom: '24px', left: '50%', transform: 'translateX(-50%)',
                    background: 'var(--bg-secondary)', border: '1px solid var(--border)',
                    borderRadius: '12px', padding: '12px 20px',
                    display: 'flex', alignItems: 'center', gap: '12px',
                    boxShadow: '0 8px 32px rgba(0,0,0,0.7)', zIndex: 100, whiteSpace: 'nowrap'
                }}>
                    <span style={{ fontWeight: 600, color: 'var(--text-secondary)' }}>
                        {filteredBooks.length} new item{filteredBooks.length !== 1 ? 's' : ''}
                    </span>
                    {(newSubFilter === 'pairs' || newSubFilter === 'all') && newPairs.length > 0 && (
                        <button
                            className="btn btn-secondary"
                            onClick={() => setShowMetadataCleanup(true)}
                            style={{ fontSize: '0.85rem', padding: '6px 14px' }}
                        >
                            Resolve Mismatches
                        </button>
                    )}
                    <button
                        className="btn btn-primary"
                        onClick={handleAcknowledgeAll}
                        disabled={acknowledging}
                        style={{ fontSize: '0.85rem', padding: '6px 14px' }}
                    >
                        {acknowledging ? 'Acknowledging...' : 'Acknowledge All'}
                    </button>
                </div>
            )}

            {showMetadataCleanup && (
                <MetadataCleanupModal
                    onClose={() => setShowMetadataCleanup(false)}
                    onComplete={() => {
                        setShowMetadataCleanup(false)
                        loadData()
                    }}
                />
            )}

            {/* Floating bulk action bar */}
            {selectMode && selectedIds.size > 0 && (
                <div style={{
                    position: 'fixed', bottom: '24px', left: '50%', transform: 'translateX(-50%)',
                    background: 'var(--bg-secondary)', border: '1px solid var(--border)',
                    borderRadius: '12px', padding: '12px 20px',
                    display: 'flex', alignItems: 'center', gap: '12px',
                    boxShadow: '0 8px 32px rgba(0,0,0,0.7)', zIndex: 100, whiteSpace: 'nowrap'
                }}>
                    <span style={{ fontWeight: 600, color: 'var(--text-secondary)' }}>
                        {selectedIds.size} selected
                    </span>
                    <button className="btn btn-secondary" onClick={() => setBulkEditOpen(true)}>Edit Metadata</button>
                    {selectedBooksForMatch && (
                        <button className="btn btn-secondary" onClick={() => setBulkMatchOpen(true)}>Bulk Match</button>
                    )}
                    <button className="btn btn-danger" onClick={() => { setBulkDeleteSourceFile(false); setBulkDeleteOpen(true) }}>Delete</button>
                </div>
            )}

            {/* Bulk Delete Confirmation */}
            {bulkDeleteOpen && (
                <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 }}>
                    <div className="card" style={{ padding: '24px', maxWidth: '480px', width: '90%' }}>
                        <h3 style={{ marginTop: 0 }}>Confirm Delete</h3>
                        <p>Are you sure you want to delete <strong>{selectedIds.size} item{selectedIds.size !== 1 ? 's' : ''}</strong>?</p>
                        <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>
                            For paired items, both the ebook and audiobook will be deleted.
                        </p>
                        <label style={{ display: 'flex', alignItems: 'center', gap: '8px', margin: '16px 0', cursor: 'pointer' }}>
                            <input type="checkbox" checked={bulkDeleteSourceFile} onChange={(e) => setBulkDeleteSourceFile(e.target.checked)} />
                            <span>Also delete the source files from disk</span>
                        </label>
                        {bulkDeleteSourceFile && (
                            <div className="alert alert-error" style={{ fontSize: '0.85rem', marginBottom: '16px' }}>
                                This will permanently delete files from your server!
                            </div>
                        )}
                        <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
                            <button className="btn btn-secondary" onClick={() => setBulkDeleteOpen(false)} disabled={bulkDeleting}>Cancel</button>
                            <button className="btn btn-danger" onClick={executeBulkDelete} disabled={bulkDeleting}>
                                {bulkDeleting ? 'Deleting...' : `Delete ${selectedIds.size}`}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* Bulk Edit Modal */}
            {bulkEditOpen && (
                <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 }}>
                    <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: '12px', padding: '24px', maxWidth: '520px', width: '90%', boxShadow: '0 8px 32px rgba(0,0,0,0.6)' }}>
                        <h3 style={{ marginTop: 0 }}>Edit {selectedIds.size} Item{selectedIds.size !== 1 ? 's' : ''}</h3>
                        <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem', marginTop: 0 }}>
                            Leave fields blank to keep existing values. Only filled fields will be updated.
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
                                {bulkSaving ? 'Saving...' : `Save to ${selectedIds.size} Item${selectedIds.size !== 1 ? 's' : ''}`}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* Mobile Upload FAB */}
            {isMobile && canEdit && (
                <div ref={mobileUploadRef} style={{ position: 'fixed', bottom: 'calc(var(--mobile-bottomnav-height, 80px) + 16px)', right: 16, zIndex: 150 }}>
                    <button
                        onClick={() => setMobileUploadOpen(o => !o)}
                        style={{
                            width: 56, height: 56, borderRadius: '50%',
                            background: 'var(--accent)', color: 'white', border: 'none',
                            boxShadow: '0 4px 20px var(--accent-glow)',
                            display: 'flex', alignItems: 'center', justifyContent: 'center',
                            cursor: 'pointer', fontSize: 28, fontWeight: 300,
                            transition: 'transform 0.2s ease',
                            transform: mobileUploadOpen ? 'rotate(45deg)' : 'none',
                        }}
                    >
                        +
                    </button>
                    {mobileUploadOpen && (
                        <div className="library-mobile-upload-menu">
                            <button onClick={() => { setMobileUploadOpen(false); ebookFileRef.current?.click() }} disabled={uploadingEbook}>
                                {uploadingEbook ? 'Uploading...' : 'Upload Ebook'}
                            </button>
                            <button onClick={() => { setMobileUploadOpen(false); audiobookFileRef.current?.click() }} disabled={uploadingAudiobook}>
                                {uploadingAudiobook ? 'Uploading...' : 'Upload Audiobook'}
                            </button>
                        </div>
                    )}
                </div>
            )}

            {/* Bulk Match Modal */}
            {bulkMatchOpen && selectedBooksForMatch && (
                <BulkMatchModal
                    books={selectedBooksForMatch.books}
                    bookType={selectedBooksForMatch.bookType}
                    onClose={() => setBulkMatchOpen(false)}
                    onUpdate={(id, patch) => {
                        if (selectedBooksForMatch.bookType === 'ebook') setEbooks(prev => prev.map(b => b.id === id ? { ...b, ...patch } : b))
                        else setAudiobooks(prev => prev.map(b => b.id === id ? { ...b, ...patch } : b))
                    }}
                />
            )}
        </div>
    )
}

const LIBRARY_SORT_LABELS = { title: 'Title', author: 'Author', series: 'Series', date: 'Date Added', size: 'Size' }

function LibrarySortPill({ sortField, setSortField, sortDir, setSortDir, sortOpen, setSortOpen, sortRef }) {
    return (
        <div className="series-sort-wrap" ref={sortRef}>
            <button
                className={`series-sort-btn${sortOpen ? ' open' : ''}`}
                onClick={() => setSortOpen(o => !o)}
            >
                Sort: {LIBRARY_SORT_LABELS[sortField]} {sortDir === 'asc' ? '↑' : '↓'}
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="12" height="12">
                    <polyline points="6 9 12 15 18 9" />
                </svg>
            </button>
            {sortOpen && (
                <div className="series-sort-dropdown">
                    {Object.entries(LIBRARY_SORT_LABELS).map(([key, label]) => (
                        <button
                            key={key}
                            className={`series-sort-option${sortField === key ? ' active' : ''}`}
                            onClick={() => setSortField(key)}
                        >
                            {label}
                            {sortField === key && (
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
    )
}

export default LibraryPage
