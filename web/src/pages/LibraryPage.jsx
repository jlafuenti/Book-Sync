import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
    getLibraryItemsPage, getLibraryFacets, fetchAllPages,
    uploadEbook, uploadAudiobook, scanLibrary,
    normalizeLibrary, updateEbookMetadata, updateAudiobookMetadata,
    rescanAllLibrary, deleteEbook, deleteAudiobook, verifyFiles,
    cleanupOrphans, acknowledgeNewItems, acknowledgeNewPairs,
    getAllProgress
} from '../api'
import { pairTargetPath } from '../utils/pairRouting'
import useLibraryBrowse from '../hooks/useLibraryBrowse'
import { toDisplayEntry, entryKey, groupProgressByPair, patchMedia } from '../lib/libraryItems'
// Server timestamps are naive UTC — parse via the shared helper (issue #216).
import { formatDate } from '../lib/datetime'
import EnhancedMetadataModal from '../components/EnhancedMetadataModal'
import BulkMatchModal from '../components/BulkMatchModal'
import FilterPill from '../components/FilterPill'
import MetadataCleanupModal from '../components/MetadataCleanupModal'
import { useAuth } from '../contexts/AuthContext'
import useIsMobile from '../hooks/useIsMobile'
import useCoverSrc from '../hooks/useCoverSrc'
import './LibraryPage.css'

// ---- Browse constants (issue #120) ----
//
// The list is server-driven: tabs, sub-filters, search, author/series pills
// and sort are query params on GET /api/library/items, and the page loads it
// PAGE_SIZE items at a time (infinite scroll, with a "Show more" fallback).
const PAGE_SIZE = 50
const SEARCH_DEBOUNCE_MS = 300
// UI sub-filter keys → the server's `kind` param.
const SUB_FILTER_KIND = { all: '', ebooks: 'ebook', audiobooks: 'audiobook', pairs: 'pair' }
const KIND_SUB_FILTER = { '': 'all', ebook: 'ebooks', audiobook: 'audiobooks', pair: 'pairs' }
const EMPTY_FACETS = {
    authors: [], series: [],
    counts: { ebooks: 0, audiobooks: 0, pairs: 0, unpaired: 0, new_ebooks: 0, new_audiobooks: 0, new_pairs: 0 },
}

function useDebouncedValue(value, delayMs) {
    const [debounced, setDebounced] = useState(value)
    useEffect(() => {
        const t = setTimeout(() => setDebounced(value), delayMs)
        return () => clearTimeout(t)
    }, [value, delayMs])
    return debounced
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

export function BookCard({ book, selectMode, isSelected, onSelect, onStartSelect, onEdit, onDelete, onNavigate, canEdit }) {
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

    const coverUrl = useCoverSrc(book.cover_path)

    const handleClick = (e) => {
        if (selectMode) { onSelect(e); return }
        onNavigate()
    }

    return (
        <div
            className={`lib-book-card${isSelected ? ' selected' : ''}${selectMode ? ' select-mode' : ''}`}
            onClick={handleClick}
        >
            <div className="lib-book-card-cover">
                <input
                    type="checkbox"
                    className="lib-book-card-checkbox"
                    checked={isSelected}
                    readOnly
                    onClick={(e) => {
                        e.stopPropagation()
                        if (selectMode) onSelect(e)
                        else onStartSelect(e)
                    }}
                />
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

export function BookRow({ book, selectMode, isSelected, onSelect, onEdit, onDelete, onNavigate, canEdit }) {
    const coverUrl = useCoverSrc(book.cover_path)

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
            <div className="lib-book-row-cell muted">{formatDate(book.uploaded_at)}</div>
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
    const [mobileUploadOpen, setMobileUploadOpen] = useState(false)
    const mobileUploadRef = useRef(null)
    const [mobileFilterOpen, setMobileFilterOpen] = useState(false)
    const mobileFilterRef = useRef(null)

    // Data state. The list itself lives in useLibraryBrowse (below); facets
    // are the filter-pill options + tab counts; progress records decide
    // `lastFormat` per pair so a pair-tap routes to whichever medium the user
    // last used.
    const [facets, setFacets] = useState(null)
    const [facetsTick, setFacetsTick] = useState(0)
    const [progress, setProgress] = useState([])
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
    const [acknowledging, setAcknowledging] = useState(false)
    const [showMetadataCleanup, setShowMetadataCleanup] = useState(false)
    const [sortOpen, setSortOpen] = useState(false)

    // ---- URL-backed browse state (issue #120) ----
    // Tab, sub-filter, sort, search and the author/series pills all live in
    // the query string: a reload or a shared link lands on the same view, and
    // the server query is derived from one place. `search` has exactly one
    // source of truth — the URL: the desktop global bar and the mobile box
    // (issue #213) both read and write it, and the query is debounced off it.
    // The route prop (`/library/ebooks`) is the default tab.
    const defaultTab = tab === 'audiobooks' ? 'audiobooks' : tab === 'ebooks' ? 'ebooks' : 'all'
    const activeFilter = searchParams.get('tab') || defaultTab
    const kindParam = searchParams.get('kind') || ''
    const unpairedSubFilter = activeFilter === 'unpaired' ? (KIND_SUB_FILTER[kindParam] || 'all') : 'all'
    const newSubFilter = activeFilter === 'new' ? (KIND_SUB_FILTER[kindParam] || 'all') : 'all'
    const sortField = searchParams.get('sort') || 'title'
    const sortDir = searchParams.get('dir') || 'asc'
    const searchTerm = searchParams.get('search') || ''
    const authorFilter = searchParams.get('author') || ''
    const seriesFilter = searchParams.get('series') || ''

    const setParams = useCallback((patch) => {
        setSearchParams(prev => {
            const n = new URLSearchParams(prev)
            for (const [k, v] of Object.entries(patch)) {
                if (v === null || v === undefined || v === '') n.delete(k)
                else n.set(k, String(v))
            }
            return n
        }, { replace: true })
    }, [setSearchParams])
    const setActiveFilter = (v) => setParams({ tab: v, kind: '' })
    const setUnpairedSubFilter = (v) => setParams({ kind: SUB_FILTER_KIND[v] || '' })
    const setNewSubFilter = (v) => setParams({ kind: SUB_FILTER_KIND[v] || '' })
    const setSortField = (v) => setParams({ sort: v })
    const setSortDir = (v) => setParams({ dir: v })
    const setAuthorFilter = (v) => setParams({ author: v })
    const setSeriesFilter = (v) => setParams({ series: v })
    const setSearchTerm = (v) => setParams({ search: v })

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

    // ---- Server-driven list (issue #120) ----
    const q = useDebouncedValue(searchTerm, SEARCH_DEBOUNCE_MS)
    const browseQuery = useMemo(() => ({
        tab: activeFilter, kind: kindParam, q, author: authorFilter, series: seriesFilter,
        sort: sortField, dir: sortDir,
    }), [activeFilter, kindParam, q, authorFilter, seriesFilter, sortField, sortDir])
    const browse = useLibraryBrowse(browseQuery, { pageSize: PAGE_SIZE })

    // Facets follow the tab (pills describe the whole tab, not the search).
    useEffect(() => {
        let cancelled = false
        getLibraryFacets({ tab: activeFilter, kind: kindParam })
            .then(f => { if (!cancelled) setFacets(f) })
            .catch(() => { if (!cancelled) setFacets(prev => prev || EMPTY_FACETS) })
        return () => { cancelled = true }
    }, [activeFilter, kindParam, facetsTick])

    const loadProgress = useCallback(() => {
        return getAllProgress().then(p => setProgress(p || [])).catch(() => setProgress([]))
    }, [])
    useEffect(() => { loadProgress() }, [loadProgress])

    // After any mutation: refetch the loaded span, the counts/pills, and progress.
    const refreshAll = useCallback(async () => {
        setFacetsTick(t => t + 1)
        await Promise.all([browse.reload(), loadProgress()])
    }, [browse.reload, loadProgress]) // eslint-disable-line react-hooks/exhaustive-deps

    const counts = (facets || EMPTY_FACETS).counts
    const allAuthors = useMemo(() => (facets || EMPTY_FACETS).authors.map(a => a.name), [facets])
    const allSeriesNames = useMemo(() => (facets || EMPTY_FACETS).series.map(s => s.name), [facets])

    // Display entries — the flat shape the cards/rows/selection consume.
    const progressByPair = useMemo(() => groupProgressByPair(progress, browse.items), [progress, browse.items])
    const filteredBooks = useMemo(
        () => browse.items.map(it => toDisplayEntry(it, progressByPair)),
        [browse.items, progressByPair],
    )
    const total = browse.total
    const loading = facets === null || (browse.loading && browse.page === 0)

    // Infinite scroll: a sentinel below the list asks for the next page as it
    // scrolls into view; the "Show more" button below it is the fallback.
    const sentinelRef = useRef(null)
    const loadMoreRef = useRef(browse.loadMore)
    useEffect(() => { loadMoreRef.current = browse.loadMore }, [browse.loadMore])
    useEffect(() => {
        const el = sentinelRef.current
        if (!el || typeof IntersectionObserver === 'undefined') return
        const io = new IntersectionObserver((entries) => {
            if (entries.some(e => e.isIntersecting)) loadMoreRef.current()
        }, { rootMargin: '400px' })
        io.observe(el)
        return () => io.disconnect()
    }, [browse.hasMore, viewMode, isMobile, loading])

    // Everything of the current tab/kind (not just the loaded pages) — for
    // "Acknowledge All", which must cover items that haven't scrolled in.
    const collectAllInTab = useCallback(() => fetchAllPages(
        (page) => getLibraryItemsPage({ tab: activeFilter, kind: kindParam, page, limit: 500 })
    ), [activeFilter, kindParam])

    // Close dropdowns on outside click
    useEffect(() => {
        const handler = (e) => {
            if (maintenanceRef.current && !maintenanceRef.current.contains(e.target)) setMaintenanceOpen(false)
            if (uploadRef.current && !uploadRef.current.contains(e.target)) setUploadOpen(false)
            if (sortRef.current && !sortRef.current.contains(e.target)) setSortOpen(false)
            if (mobileUploadRef.current && !mobileUploadRef.current.contains(e.target)) setMobileUploadOpen(false)
            if (mobileFilterRef.current && !mobileFilterRef.current.contains(e.target)) setMobileFilterOpen(false)
        }
        document.addEventListener('mousedown', handler)
        return () => document.removeEventListener('mousedown', handler)
    }, [])

    // ---- Selection helpers ----

    const bookKey = entryKey

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
                const entry = filteredBooks.find(b => b.mediaType === 'pair' && b.pair_id === pairId)
                if (!entry) return []
                const results = []
                if (entry.ebook_id) results.push({ mediaType: 'ebook', id: entry.ebook_id })
                if (entry.audiobook_id) results.push({ mediaType: 'audiobook', id: entry.audiobook_id })
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
            browse.patchItems(items => selected.reduce((acc, s) => patchMedia(acc, s.mediaType, s.id, patch), items))
            setFacetsTick(t => t + 1)
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
            await refreshAll()
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
        try { const r = await scanLibrary(); setScanResult(r); await refreshAll() }
        catch (err) { setError(err.message) }
        finally { setScanning(false) }
    }

    const handleNormalize = async () => {
        setNormalizing(true); setScanResult(null); setError('')
        try { const r = await normalizeLibrary(); setScanResult(r); await refreshAll() }
        catch (err) { setError(err.message) }
        finally { setNormalizing(false) }
    }

    const handleRescanAll = async () => {
        if (!window.confirm("WARNING: This will force a complete rescan of EVERY file in your library, overwriting all current database metadata with whatever tags are physically embedded inside the files. This cannot be undone!\n\nAre you sure?")) return
        setRescanningAll(true); setScanResult(null); setError('')
        try { const r = await rescanAllLibrary(); setScanResult(r); await refreshAll() }
        catch (err) { setError(err.message) }
        finally { setRescanningAll(false) }
    }

    const handleEbookUpload = async (e) => {
        const file = e.target.files[0]; if (!file) return
        setUploadingEbook(true); setError('')
        try { await uploadEbook(file); await refreshAll() }
        catch (err) { setError(err.message) }
        finally { setUploadingEbook(false); ebookFileRef.current.value = '' }
    }

    const handleAudiobookUpload = async (e) => {
        const file = e.target.files[0]; if (!file) return
        setUploadingAudiobook(true); setError('')
        try { await uploadAudiobook(file); await refreshAll() }
        catch (err) { setError(err.message) }
        finally { setUploadingAudiobook(false); audiobookFileRef.current.value = '' }
    }

    const handleSaveMetadata = async (bookId, meta) => {
        try {
            if (editingType === 'ebook') {
                await updateEbookMetadata(bookId, meta)
            } else {
                await updateAudiobookMetadata(bookId, meta)
            }
            browse.patchItems(items => patchMedia(items, editingType, bookId, meta))
            setFacetsTick(t => t + 1)
            setEditingBook(null); setEditingType(null)
        } catch (err) {
            alert('Failed to update metadata: ' + err.message)
        }
    }

    const openEdit = (book) => {
        if (book.mediaType === 'pair') {
            // Edit the ebook component of the pair
            const eb = book.pair?.ebook
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
            } else {
                await deleteAudiobook(deleteTarget.id, deleteSourceFile)
            }
            await refreshAll()
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
            // Everything new in this sub-filter — including items that
            // haven't scrolled in — not just the loaded pages.
            const all = await collectAllInTab()
            const ebookIds = all.filter(it => it.kind === 'ebook').map(it => it.ebook.id)
            const audioIds = all.filter(it => it.kind === 'audiobook').map(it => it.audiobook.id)
            let pairIds = all.filter(it => it.kind === 'pair').map(it => it.pair.id)
            if (newSubFilter === 'all') {
                // The "All" sub-filter lists media; new pairs are acknowledged too.
                const newPairs = await fetchAllPages(
                    (page) => getLibraryItemsPage({ tab: 'new', kind: 'pair', page, limit: 500 }))
                pairIds = newPairs.map(it => it.pair.id)
            }
            await Promise.all([
                ...(ebookIds.length || audioIds.length ? [acknowledgeNewItems(ebookIds, audioIds)] : []),
                ...(pairIds.length ? [acknowledgeNewPairs(pairIds)] : []),
            ])
            await refreshAll()
        } catch (err) {
            alert('Failed to acknowledge: ' + err.message)
        } finally {
            setAcknowledging(false)
        }
    }

    const handleAcknowledgeSelected = async () => {
        const parsed = parseSelectedIds()
        const ebookIds = parsed.filter(p => p.mediaType === 'ebook').map(p => p.id)
        const audiobookIds = parsed.filter(p => p.mediaType === 'audiobook').map(p => p.id)
        try {
            await acknowledgeNewItems(ebookIds, audiobookIds)
        } catch (err) {
            alert('Failed to acknowledge: ' + err.message)
            return
        }
        exitSelectMode()
        await refreshAll()
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
            await refreshAll()
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
        const ids = new Set(parsed.map(s => s.id))
        return {
            books: filteredBooks.filter(b => b.mediaType === type && ids.has(b.id)),
            bookType: type,
        }
    }, [selectedIds, filteredBooks]) // eslint-disable-line react-hooks/exhaustive-deps

    // ---- Render ----

    if (loading) {
        return <div className="loading-page"><div className="spinner"></div> Loading library...</div>
    }

    const newCount = counts.new_ebooks + counts.new_audiobooks + counts.new_pairs
    const filterPills = [
        { key: 'all', label: 'All', count: counts.pairs + counts.unpaired },
        { key: 'ebooks', label: 'Ebooks', count: counts.ebooks },
        { key: 'audiobooks', label: 'Audiobooks', count: counts.audiobooks },
        { key: 'paired', label: 'Paired', count: counts.pairs },
        { key: 'unpaired', label: 'Unpaired', count: counts.unpaired },
        ...(newCount > 0 || activeFilter === 'new' ? [{ key: 'new', label: 'New', count: newCount }] : []),
    ]
    const activePill = filterPills.find(p => p.key === activeFilter) || filterPills[0]

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
                                onClick={() => setActiveFilter(p.key)}
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
                                    <button className="library-dropdown-item" onClick={() => { setMaintenanceOpen(false); setShowMetadataCleanup(true) }}>
                                        Resolve Mismatches
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

            {/* Mobile filter dropdown — replaces desktop filter pills toolbar */}
            {isMobile && (
                <div className="library-mobile-filter-wrap" ref={mobileFilterRef}>
                    <button
                        className={`library-mobile-filter-btn${mobileFilterOpen ? ' open' : ''}`}
                        onClick={() => setMobileFilterOpen(o => !o)}
                    >
                        {activePill.label}
                        <span style={{ color: 'var(--text-muted)', fontWeight: 400, fontSize: '0.8rem' }}>({activePill.count})</span>
                        <span className="material-symbols-outlined">expand_more</span>
                    </button>
                    {mobileFilterOpen && (
                        <div className="library-mobile-filter-dropdown">
                            {filterPills.map(p => (
                                <button
                                    key={p.key}
                                    className={`library-mobile-filter-option${activeFilter === p.key ? ' active' : ''}`}
                                    onClick={() => {
                                        setActiveFilter(p.key)
                                        setMobileFilterOpen(false)
                                    }}
                                >
                                    {p.label}
                                    <span className="filter-opt-count">{p.count}</span>
                                    {activeFilter === p.key && (
                                        <span className="material-symbols-outlined filter-opt-check">check</span>
                                    )}
                                </button>
                            ))}
                        </div>
                    )}
                </div>
            )}

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
                        { key: 'all', label: `All (${newCount})` },
                        { key: 'ebooks', label: `Ebooks (${counts.new_ebooks})` },
                        { key: 'audiobooks', label: `Audiobooks (${counts.new_audiobooks})` },
                        { key: 'pairs', label: `Pairs (${counts.new_pairs})` },
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

            {/* Mobile search input — URL-backed, and the only way to clear
                `?search=` on a phone (the desktop bar and toolbar chip are
                both hidden below 768px). See issue #213. */}
            {isMobile && (
                <div className="library-mobile-search">
                    <span className="search-icon material-symbols-outlined" style={{ fontSize: 20 }}>search</span>
                    <input
                        type="text"
                        placeholder="Search books..."
                        aria-label="Search books"
                        value={searchTerm}
                        onChange={e => setSearchTerm(e.target.value)}
                    />
                    {searchTerm && (
                        <button
                            type="button"
                            className="library-mobile-search-clear"
                            aria-label="Clear search"
                            onClick={() => setSearchTerm('')}
                        >
                            ✕
                        </button>
                    )}
                </div>
            )}

            {/* Mobile view controls */}
            {isMobile && (
                <div className="library-mobile-controls">
                    <span className="showing-count">Showing {filteredBooks.length} of {total}</span>
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
                <span className="library-stat"><strong>{counts.ebooks}</strong> Ebooks</span>
                <span className="library-stat"><strong>{counts.audiobooks}</strong> Audiobooks</span>
                <span className="library-stat"><strong>{counts.pairs}</strong> Paired</span>
                <span className="library-stat">Showing <strong>{filteredBooks.length}</strong> of <strong>{total}</strong></span>
                {browse.loading && filteredBooks.length > 0 && <span className="library-stat muted">Updating…</span>}
                {browse.error && <span className="library-stat" style={{ color: 'var(--danger, #e66)' }}>{browse.error}</span>}
            </div>

            {/* Book display */}
            {filteredBooks.length === 0 ? (
                browse.loading ? (
                    <div className="loading-page"><div className="spinner"></div></div>
                ) : (
                    <div className="library-empty">
                        <div className="library-empty-icon">
                            {counts.ebooks + counts.audiobooks === 0 ? '📚' : '🔍'}
                        </div>
                        <h3>{counts.ebooks + counts.audiobooks === 0 ? 'No books yet' : 'No books match your filters'}</h3>
                        <p>{counts.ebooks + counts.audiobooks === 0
                            ? 'Scan your directories or upload books to get started.'
                            : 'Try adjusting your search or filters.'
                        }</p>
                    </div>
                )
            ) : (viewMode === 'grid' || isMobile) ? (
                /* ---- Grid View ---- */
                <>
                    <div className="library-grid">
                        {filteredBooks.map((book, idx) => (
                            <BookCard
                                key={bookKey(book)}
                                book={book}
                                selectMode={selectMode}
                                isSelected={selectedIds.has(bookKey(book))}
                                onSelect={(e) => handleSelect(book, idx, e?.shiftKey)}
                                onStartSelect={(e) => { setSelectMode(true); handleSelect(book, idx, e?.shiftKey) }}
                                onEdit={() => openEdit(book)}
                                onDelete={() => openDeleteModal(book)}
                                onNavigate={() => navigate(
                                    book.mediaType === 'pair'
                                        ? pairTargetPath(book, book.lastFormat)
                                        : `/book/${book.mediaType}/${book.id}`
                                )}
                                canEdit={canEdit}
                            />
                        ))}
                    </div>
                    {browse.hasMore && (
                        <div style={{ textAlign: 'center', marginTop: '24px' }}>
                            <div ref={sentinelRef} className="library-scroll-sentinel" aria-hidden="true" />
                            <button
                                className="btn btn-secondary"
                                onClick={() => browse.loadMore()}
                                disabled={browse.loadingMore}
                                style={{ padding: '8px 32px' }}
                            >
                                {browse.loadingMore ? 'Loading…' : `Show more (${total - filteredBooks.length} remaining)`}
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
                                    ? pairTargetPath(book, book.lastFormat)
                                    : `/book/${book.mediaType}/${book.id}`
                            )}
                            canEdit={canEdit}
                        />
                    ))}
                    {browse.hasMore && (
                        <div style={{ textAlign: 'center', padding: '16px 0' }}>
                            <div ref={sentinelRef} className="library-scroll-sentinel" aria-hidden="true" />
                            <button
                                className="btn btn-secondary"
                                onClick={() => browse.loadMore()}
                                disabled={browse.loadingMore}
                                style={{ padding: '8px 32px' }}
                            >
                                {browse.loadingMore ? 'Loading…' : `Show more (${total - filteredBooks.length} remaining)`}
                            </button>
                        </div>
                    )}
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
            {activeFilter === 'new' && total > 0 && !selectMode && (
                <div style={{
                    position: 'fixed', bottom: '24px', left: '50%', transform: 'translateX(-50%)',
                    background: 'var(--bg-secondary)', border: '1px solid var(--border)',
                    borderRadius: '12px', padding: '12px 20px',
                    display: 'flex', alignItems: 'center', gap: '12px',
                    boxShadow: '0 8px 32px rgba(0,0,0,0.7)', zIndex: 100, whiteSpace: 'nowrap'
                }}>
                    <span style={{ fontWeight: 600, color: 'var(--text-secondary)' }}>
                        {total} new item{total !== 1 ? 's' : ''}
                    </span>
                    {(newSubFilter === 'pairs' || newSubFilter === 'all') && counts.new_pairs > 0 && (
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
                        refreshAll()
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
                    {activeFilter === 'new' && (
                        <button className="btn btn-primary" onClick={handleAcknowledgeSelected}>Acknowledge Selected</button>
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
                        browse.patchItems(items => patchMedia(items, selectedBooksForMatch.bookType, id, patch))
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
