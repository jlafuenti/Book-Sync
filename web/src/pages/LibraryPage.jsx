import React, { useState, useEffect, useRef, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { getEbooks, getAudiobooks, uploadEbook, uploadAudiobook, scanLibrary, normalizeLibrary, updateEbookMetadata, updateAudiobookMetadata, rescanAllLibrary, deleteEbook, deleteAudiobook, verifyFiles, cleanupOrphans, applyRemoteCover } from '../api'
import EnhancedMetadataModal from '../components/EnhancedMetadataModal'
import BulkMatchModal from '../components/BulkMatchModal'

// Tri-state sort: null → 'asc' → 'desc' → null
function nextSortDir(current) {
    if (!current) return 'asc'
    if (current === 'asc') return 'desc'
    return null
}

function sortBooks(books, sortCol, sortDir) {
    if (!sortCol || !sortDir) return books
    const sorted = [...books].sort((a, b) => {
        let valA, valB

        if (sortCol === 'series') {
            // Sort by series name first, then by series_index within same series
            const sA = (a.series || '').toLowerCase()
            const sB = (b.series || '').toLowerCase()
            if (!a.series && !b.series) return 0
            if (!a.series) return 1  // blanks to bottom
            if (!b.series) return -1
            if (sA !== sB) {
                valA = sA
                valB = sB
            } else {
                // Same series — sort by index
                valA = a.series_index ?? 999999
                valB = b.series_index ?? 999999
                const diff = valA - valB
                return sortDir === 'asc' ? diff : -diff
            }
        } else if (sortCol === 'file_size') {
            valA = a.file_size ?? 0
            valB = b.file_size ?? 0
            const diff = valA - valB
            return sortDir === 'asc' ? diff : -diff
        } else if (sortCol === 'uploaded_at') {
            valA = new Date(a.uploaded_at || 0).getTime()
            valB = new Date(b.uploaded_at || 0).getTime()
            const diff = valA - valB
            return sortDir === 'asc' ? diff : -diff
        } else {
            // String columns: title, author, format
            valA = (a[sortCol] || '').toLowerCase()
            valB = (b[sortCol] || '').toLowerCase()
            if (!a[sortCol] && !b[sortCol]) return 0
            if (!a[sortCol]) return 1
            if (!b[sortCol]) return -1
        }

        if (valA < valB) return sortDir === 'asc' ? -1 : 1
        if (valA > valB) return sortDir === 'asc' ? 1 : -1
        return 0
    })
    return sorted
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

function LibraryPage({ tab }) {
    const [ebooks, setEbooks] = useState([])
    const [audiobooks, setAudiobooks] = useState([])
    const [loading, setLoading] = useState(true)
    const [scanning, setScanning] = useState(false)
    const [normalizing, setNormalizing] = useState(false)
    const [scanResult, setScanResult] = useState(null)
    const [error, setError] = useState('')

    const [uploadingEbook, setUploadingEbook] = useState(false)
    const [uploadingAudiobook, setUploadingAudiobook] = useState(false)

    // Edit state
    const [editingBook, setEditingBook] = useState(null)
    const [editingType, setEditingType] = useState(null)
    const [rescanningAll, setRescanningAll] = useState(false)

    // Delete state
    const [deleteTarget, setDeleteTarget] = useState(null) // { id, title, type: 'ebook'|'audiobook' }
    const [deleteSourceFile, setDeleteSourceFile] = useState(false)
    const [deleting, setDeleting] = useState(false)

    // Verify state
    const [verifying, setVerifying] = useState(false)
    const [verifyReport, setVerifyReport] = useState(null) // { orphaned_ebooks, orphaned_audiobooks }
    const [selectedOrphanEbooks, setSelectedOrphanEbooks] = useState(new Set())
    const [selectedOrphanAudiobooks, setSelectedOrphanAudiobooks] = useState(new Set())
    const [cleaningUp, setCleaningUp] = useState(false)

    // Filtering state
    const [searchTerm, setSearchTerm] = useState('')
    const [selectedAuthor, setSelectedAuthor] = useState('')
    const [selectedSeries, setSelectedSeries] = useState('')

    // Sort state — separate for each table
    const [ebookSort, setEbookSort] = useState({ col: null, dir: null })
    const [audiobookSort, setAudiobookSort] = useState({ col: null, dir: null })

    const ebookFileRef = useRef(null)
    const audiobookFileRef = useRef(null)

    // Maintenance dropdown
    const [maintenanceOpen, setMaintenanceOpen] = useState(false)
    const maintenanceRef = useRef(null)

    // Select mode & bulk actions
    const [selectMode, setSelectMode] = useState(false)
    const [selectedIds, setSelectedIds] = useState(new Set())
    const [bulkEditOpen, setBulkEditOpen] = useState(false)
    const [bulkEditFields, setBulkEditFields] = useState({ author: '', series: '', series_index: '', publisher: '', published_year: '' })
    const [bulkSaving, setBulkSaving] = useState(false)
    const [bulkMatchOpen, setBulkMatchOpen] = useState(false)

    // Determine which tab to show
    const activeTab = tab || 'ebooks'

    // Compute unique authors and series for filters based on active tab
    const currentBooks = activeTab === 'ebooks' ? ebooks : audiobooks
    const uniqueAuthors = [...new Set(currentBooks.map(b => b.author).filter(Boolean))].sort()
    const uniqueSeries = [...new Set(currentBooks.map(b => b.series).filter(Boolean))].sort()

    // Filter logic
    const filterBook = (book) => {
        if (searchTerm) {
            const term = searchTerm.toLowerCase()
            const matchTitle = book.title?.toLowerCase().includes(term)
            const matchAuthor = book.author?.toLowerCase().includes(term)
            const matchSeries = book.series?.toLowerCase().includes(term)
            if (!matchTitle && !matchAuthor && !matchSeries) return false
        }
        if (selectedAuthor && book.author !== selectedAuthor) return false
        if (selectedSeries && book.series !== selectedSeries) return false
        return true
    }

    const filteredEbooks = useMemo(
        () => sortBooks(ebooks.filter(filterBook), ebookSort.col, ebookSort.dir),
        [ebooks, searchTerm, selectedAuthor, selectedSeries, ebookSort]
    )
    const filteredAudiobooks = useMemo(
        () => sortBooks(audiobooks.filter(filterBook), audiobookSort.col, audiobookSort.dir),
        [audiobooks, searchTerm, selectedAuthor, selectedSeries, audiobookSort]
    )

    const handleEbookSort = (col) => {
        setEbookSort(prev => ({
            col: prev.col === col && prev.dir === 'desc' ? null : col,
            dir: prev.col === col ? nextSortDir(prev.dir) : 'asc'
        }))
    }
    const handleAudiobookSort = (col) => {
        setAudiobookSort(prev => ({
            col: prev.col === col && prev.dir === 'desc' ? null : col,
            dir: prev.col === col ? nextSortDir(prev.dir) : 'asc'
        }))
    }

    const loadData = async () => {
        try {
            const [e, a] = await Promise.all([getEbooks(), getAudiobooks()])
            setEbooks(e)
            setAudiobooks(a)
        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => { loadData() }, [])

    // Close maintenance dropdown on outside click
    useEffect(() => {
        const handler = (e) => {
            if (maintenanceRef.current && !maintenanceRef.current.contains(e.target)) {
                setMaintenanceOpen(false)
            }
        }
        document.addEventListener('mousedown', handler)
        return () => document.removeEventListener('mousedown', handler)
    }, [])

    // Select mode helpers
    const toggleSelect = (id) => {
        setSelectedIds(prev => {
            const next = new Set(prev)
            next.has(id) ? next.delete(id) : next.add(id)
            return next
        })
    }

    const toggleSelectAll = (books) => {
        const allSelected = books.length > 0 && books.every(b => selectedIds.has(b.id))
        setSelectedIds(allSelected ? new Set() : new Set(books.map(b => b.id)))
    }

    const exitSelectMode = () => {
        setSelectMode(false)
        setSelectedIds(new Set())
    }

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
            const updateFn = activeTab === 'ebooks' ? updateEbookMetadata : updateAudiobookMetadata
            const setBooks = activeTab === 'ebooks' ? setEbooks : setAudiobooks
            await Promise.all([...selectedIds].map(id => updateFn(id, patch)))
            setBooks(prev => prev.map(b => selectedIds.has(b.id) ? { ...b, ...patch } : b))
            setBulkEditOpen(false)
            setBulkEditFields({ author: '', series: '', series_index: '', publisher: '', published_year: '' })
            exitSelectMode()
        } catch (err) {
            alert('Bulk edit failed: ' + err.message)
        } finally {
            setBulkSaving(false)
        }
    }

    const handleBulkDelete = async () => {
        const n = selectedIds.size
        if (!window.confirm(`Delete ${n} book${n !== 1 ? 's' : ''} from BookSync? This will also remove associated pairs, sync maps, and bookmarks.`)) return
        try {
            const deleteFn = activeTab === 'ebooks' ? deleteEbook : deleteAudiobook
            const setBooks = activeTab === 'ebooks' ? setEbooks : setAudiobooks
            await Promise.all([...selectedIds].map(id => deleteFn(id, false)))
            setBooks(prev => prev.filter(b => !selectedIds.has(b.id)))
            exitSelectMode()
        } catch (err) {
            alert('Delete failed: ' + err.message)
        }
    }

    const handleScan = async () => {
        setScanning(true)
        setScanResult(null)
        setError('')
        try {
            const result = await scanLibrary()
            setScanResult(result)
            await loadData()
        } catch (err) {
            setError(err.message)
        } finally {
            setScanning(false)
        }
    }

    const handleNormalize = async () => {
        setNormalizing(true)
        setScanResult(null)
        setError('')
        try {
            const result = await normalizeLibrary()
            setScanResult(result)
            await loadData()
        } catch (err) {
            setError(err.message)
        } finally {
            setNormalizing(false)
        }
    }

    const handleRescanAll = async () => {
        if (!window.confirm("WARNING: This will force a complete rescan of EVERY file in your library, overwriting all current database metadata (titles, authors, series, etc) with whatever tags are physically embedded inside the files. This cannot be undone!\n\nAre you sure you want to completely overwrite your metadata?")) {
            return;
        }
        setRescanningAll(true)
        setScanResult(null)
        setError('')
        try {
            const result = await rescanAllLibrary()
            setScanResult(result)
            await loadData()
        } catch (err) {
            setError(err.message)
        } finally {
            setRescanningAll(false)
        }
    }

    const handleEbookUpload = async (e) => {
        const file = e.target.files[0]
        if (!file) return
        setUploadingEbook(true)
        setError('')
        try {
            await uploadEbook(file)
            await loadData()
        } catch (err) {
            setError(err.message)
        } finally {
            setUploadingEbook(false)
            ebookFileRef.current.value = ''
        }
    }

    const handleAudiobookUpload = async (e) => {
        const file = e.target.files[0]
        if (!file) return
        setUploadingAudiobook(true)
        setError('')
        try {
            await uploadAudiobook(file)
            await loadData()
        } catch (err) {
            setError(err.message)
        } finally {
            setUploadingAudiobook(false)
            audiobookFileRef.current.value = ''
        }
    }

    const handleSaveMetadata = async (bookId, meta) => {
        try {
            if (editingType === 'ebook') {
                await updateEbookMetadata(bookId, meta)
            } else {
                await updateAudiobookMetadata(bookId, meta)
            }
            // Refresh local state without full reload
            if (editingType === 'ebook') {
                setEbooks(prev => prev.map(b => b.id === bookId ? { ...b, ...meta } : b))
            } else {
                setAudiobooks(prev => prev.map(b => b.id === bookId ? { ...b, ...meta } : b))
            }
            setEditingBook(null)
            setEditingType(null)
        } catch (err) {
            console.error(err)
            alert('Failed to update metadata: ' + err.message)
        }
    }

    const openEdit = (book, type) => {
        setEditingBook({ ...book })
        setEditingType(type)
    }

    // --- Delete handlers ---
    const openDeleteModal = (book, type) => {
        setDeleteTarget({ id: book.id, title: book.title, author: book.author, type })
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
            setDeleteTarget(null)
        } catch (err) {
            alert('Failed to delete: ' + err.message)
        } finally {
            setDeleting(false)
        }
    }

    // --- Verify handlers ---
    const handleVerify = async () => {
        setVerifying(true)
        setVerifyReport(null)
        setError('')
        try {
            const report = await verifyFiles()
            setVerifyReport(report)
            setSelectedOrphanEbooks(new Set())
            setSelectedOrphanAudiobooks(new Set())
        } catch (err) {
            setError(err.message)
        } finally {
            setVerifying(false)
        }
    }

    const toggleOrphanEbook = (id) => {
        setSelectedOrphanEbooks(prev => {
            const next = new Set(prev)
            next.has(id) ? next.delete(id) : next.add(id)
            return next
        })
    }

    const toggleOrphanAudiobook = (id) => {
        setSelectedOrphanAudiobooks(prev => {
            const next = new Set(prev)
            next.has(id) ? next.delete(id) : next.add(id)
            return next
        })
    }

    const selectAllOrphans = () => {
        if (!verifyReport) return
        setSelectedOrphanEbooks(new Set(verifyReport.orphaned_ebooks.map(e => e.id)))
        setSelectedOrphanAudiobooks(new Set(verifyReport.orphaned_audiobooks.map(a => a.id)))
    }

    const deselectAllOrphans = () => {
        setSelectedOrphanEbooks(new Set())
        setSelectedOrphanAudiobooks(new Set())
    }

    const handleCleanup = async () => {
        if (selectedOrphanEbooks.size === 0 && selectedOrphanAudiobooks.size === 0) return
        setCleaningUp(true)
        try {
            const result = await cleanupOrphans([...selectedOrphanEbooks], [...selectedOrphanAudiobooks])
            // Remove cleaned items from local state too
            setEbooks(prev => prev.filter(b => !selectedOrphanEbooks.has(b.id)))
            setAudiobooks(prev => prev.filter(b => !selectedOrphanAudiobooks.has(b.id)))
            // Remove cleaned items from report
            setVerifyReport(prev => ({
                orphaned_ebooks: prev.orphaned_ebooks.filter(e => !selectedOrphanEbooks.has(e.id)),
                orphaned_audiobooks: prev.orphaned_audiobooks.filter(a => !selectedOrphanAudiobooks.has(a.id)),
            }))
            setSelectedOrphanEbooks(new Set())
            setSelectedOrphanAudiobooks(new Set())
            setScanResult({ message: result.message })
        } catch (err) {
            alert('Cleanup failed: ' + err.message)
        } finally {
            setCleaningUp(false)
        }
    }

    const formatSize = (bytes) => {
        if (!bytes) return '—'
        if (bytes < 1024) return `${bytes} B`
        if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`
        return `${(bytes / 1048576).toFixed(1)} MB`
    }

    if (loading) {
        return <div className="loading-page"><div className="spinner"></div> Loading library...</div>
    }

    return (
        <div>
            <div className="page-header">
                <h2>Library</h2>
                <p>Manage your ebooks and audiobooks</p>
            </div>

            {error && <div className="alert alert-error">⚠️ {error}</div>}
            {scanResult && (
                <div className="alert alert-success">
                    ✅ {scanResult.message}
                </div>
            )}

            {/* Stats */}
            <div className="stat-grid">
                <div className="stat-card">
                    <div className="stat-icon purple">📚</div>
                    <div>
                        <div className="stat-value">{ebooks.length}</div>
                        <div className="stat-label">EBooks</div>
                    </div>
                </div>
                <div className="stat-card">
                    <div className="stat-icon blue">🎧</div>
                    <div>
                        <div className="stat-value">{audiobooks.length}</div>
                        <div className="stat-label">Audiobooks</div>
                    </div>
                </div>
            </div>

            {/* Actions */}
            <div style={{ display: 'flex', gap: '12px', marginBottom: '24px', flexWrap: 'wrap', alignItems: 'center' }}>
                <button
                    className="btn btn-primary"
                    onClick={handleScan}
                    disabled={scanning || normalizing || rescanningAll || selectMode}
                >
                    {scanning ? <><div className="spinner"></div> Scanning...</> : '🔍 Scan Directories'}
                </button>

                {activeTab === 'ebooks' && (
                    <button
                        className="btn btn-secondary"
                        onClick={() => ebookFileRef.current?.click()}
                        disabled={uploadingEbook || selectMode}
                    >
                        {uploadingEbook ? <><div className="spinner"></div> Uploading...</> : '📄 Upload EBook'}
                    </button>
                )}
                {activeTab === 'audiobooks' && (
                    <button
                        className="btn btn-secondary"
                        onClick={() => audiobookFileRef.current?.click()}
                        disabled={uploadingAudiobook || selectMode}
                    >
                        {uploadingAudiobook ? <><div className="spinner"></div> Uploading...</> : '🎵 Upload Audiobook'}
                    </button>
                )}

                {/* Maintenance dropdown */}
                <div style={{ position: 'relative' }} ref={maintenanceRef}>
                    <button
                        className="btn btn-secondary"
                        onClick={() => setMaintenanceOpen(o => !o)}
                        disabled={scanning || normalizing || rescanningAll || selectMode}
                    >
                        ⚙️ Maintenance ▾
                    </button>
                    {maintenanceOpen && (
                        <div style={{
                            position: 'absolute', top: '100%', left: 0, marginTop: '4px',
                            background: 'var(--bg-card)', border: '1px solid var(--border)',
                            borderRadius: '8px', boxShadow: '0 8px 24px rgba(0,0,0,0.6)',
                            zIndex: 200, minWidth: '220px', overflow: 'hidden'
                        }}>
                            <button
                                style={{ width: '100%', textAlign: 'left', border: 'none', padding: '11px 16px', background: 'transparent', color: 'var(--text-primary)', cursor: 'pointer', fontSize: '0.9rem' }}
                                onMouseEnter={e => e.currentTarget.style.background = 'var(--bg-card-hover)'}
                                onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                                onClick={() => { setMaintenanceOpen(false); handleNormalize() }}
                                disabled={normalizing}
                                title="Fix author names like 'Butcher, Jim' → 'Jim Butcher' and series like 'Dresden Files, The' → 'The Dresden Files'"
                            >
                                {normalizing ? <><div className="spinner"></div> Normalizing...</> : '🔄 Normalize Metadata'}
                            </button>
                            <button
                                style={{ width: '100%', textAlign: 'left', border: 'none', padding: '11px 16px', background: 'transparent', color: 'var(--error)', cursor: 'pointer', fontSize: '0.9rem' }}
                                onMouseEnter={e => e.currentTarget.style.background = 'var(--bg-card-hover)'}
                                onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                                onClick={() => { setMaintenanceOpen(false); handleRescanAll() }}
                                disabled={rescanningAll}
                                title="Forcefully extract metadata from EVERY file and overwrite the database. Destructive!"
                            >
                                {rescanningAll ? <><div className="spinner"></div> Overwriting...</> : '⚠️ Force Rescan All'}
                            </button>
                            <button
                                style={{ width: '100%', textAlign: 'left', border: 'none', padding: '11px 16px', background: 'transparent', color: 'var(--text-primary)', cursor: 'pointer', fontSize: '0.9rem' }}
                                onMouseEnter={e => e.currentTarget.style.background = 'var(--bg-card-hover)'}
                                onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                                onClick={() => { setMaintenanceOpen(false); handleVerify() }}
                                disabled={verifying}
                                title="Check all books against their source files and find orphaned entries"
                            >
                                {verifying ? <><div className="spinner"></div> Verifying...</> : '🔎 Verify Files'}
                            </button>
                        </div>
                    )}
                </div>

                {/* Select mode toggle */}
                <button
                    className={`btn ${selectMode ? 'btn-primary' : 'btn-secondary'}`}
                    onClick={() => selectMode ? exitSelectMode() : setSelectMode(true)}
                    style={{ marginLeft: 'auto' }}
                >
                    {selectMode ? `✕ Cancel${selectedIds.size > 0 ? ` (${selectedIds.size})` : ''}` : '☑ Bulk Edit'}
                </button>

                <input ref={ebookFileRef} type="file" accept=".epub,.pdf,.mobi" hidden onChange={handleEbookUpload} />
                <input ref={audiobookFileRef} type="file" accept=".mp3,.m4a,.m4b,.flac,.ogg,.wav" hidden onChange={handleAudiobookUpload} />
            </div>

            {/* Verify Report */}
            {verifyReport && (
                <div className="card" style={{ marginBottom: '24px', padding: '20px' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                        <h3 style={{ margin: 0 }}>🔎 File Verification Report</h3>
                        <button className="btn btn-secondary" onClick={() => setVerifyReport(null)} style={{ padding: '4px 12px' }}>✕ Close</button>
                    </div>
                    {verifyReport.orphaned_ebooks.length === 0 && verifyReport.orphaned_audiobooks.length === 0 ? (
                        <div className="alert alert-success">✅ All files verified — no orphaned entries found!</div>
                    ) : (
                        <>
                            <div className="alert alert-error" style={{ marginBottom: '16px' }}>
                                ⚠️ Found {verifyReport.orphaned_ebooks.length} orphaned ebook(s) and {verifyReport.orphaned_audiobooks.length} orphaned audiobook(s) with missing source files.
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
                                    {cleaningUp ? <><div className="spinner"></div> Cleaning...</> : `🗑️ Delete Selected (${selectedOrphanEbooks.size + selectedOrphanAudiobooks.size})`}
                                </button>
                            </div>
                            {verifyReport.orphaned_ebooks.length > 0 && (
                                <>
                                    <h4 style={{ margin: '12px 0 8px' }}>📚 Orphaned EBooks</h4>
                                    <div className="table-wrapper">
                                        <table>
                                            <thead>
                                                <tr>
                                                    <th style={{ width: '40px' }}></th>
                                                    <th>Title</th>
                                                    <th>Author</th>
                                                    <th>File Path</th>
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {verifyReport.orphaned_ebooks.map(e => (
                                                    <tr key={e.id}>
                                                        <td><input type="checkbox" checked={selectedOrphanEbooks.has(e.id)} onChange={() => toggleOrphanEbook(e.id)} /></td>
                                                        <td style={{ fontWeight: 500 }}>{e.title}</td>
                                                        <td style={{ color: 'var(--text-secondary)' }}>{e.author || '—'}</td>
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
                                    <h4 style={{ margin: '12px 0 8px' }}>🎧 Orphaned Audiobooks</h4>
                                    <div className="table-wrapper">
                                        <table>
                                            <thead>
                                                <tr>
                                                    <th style={{ width: '40px' }}></th>
                                                    <th>Title</th>
                                                    <th>Author</th>
                                                    <th>File Path</th>
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {verifyReport.orphaned_audiobooks.map(a => (
                                                    <tr key={a.id}>
                                                        <td><input type="checkbox" checked={selectedOrphanAudiobooks.has(a.id)} onChange={() => toggleOrphanAudiobook(a.id)} /></td>
                                                        <td style={{ fontWeight: 500 }}>{a.title}</td>
                                                        <td style={{ color: 'var(--text-secondary)' }}>{a.author || '—'}</td>
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

            {/* Filters */}
            <div className="card" style={{ marginBottom: '24px', padding: '20px' }}>
                <div style={{ display: 'flex', gap: '15px', flexWrap: 'wrap' }}>
                    <div style={{ flex: 1, minWidth: '200px' }}>
                        <label style={{ display: 'block', marginBottom: '5px', fontSize: '0.9rem', fontWeight: 500 }}>Search</label>
                        <input
                            type="text"
                            placeholder="Search title, author..."
                            className="form-input"
                            value={searchTerm}
                            onChange={e => setSearchTerm(e.target.value)}
                        />
                    </div>
                    <div style={{ flex: 1, minWidth: '200px' }}>
                        <label style={{ display: 'block', marginBottom: '5px', fontSize: '0.9rem', fontWeight: 500 }}>Author</label>
                        <select
                            className="form-input"
                            value={selectedAuthor}
                            onChange={e => {
                                setSelectedAuthor(e.target.value)
                                setSelectedSeries('') // Reset series when author changes
                            }}
                        >
                            <option value="">All Authors</option>
                            {uniqueAuthors.map(author => (
                                <option key={author} value={author}>{author}</option>
                            ))}
                        </select>
                    </div>
                    <div style={{ flex: 1, minWidth: '200px' }}>
                        <label style={{ display: 'block', marginBottom: '5px', fontSize: '0.9rem', fontWeight: 500 }}>Series</label>
                        <select
                            className="form-input"
                            value={selectedSeries}
                            onChange={e => setSelectedSeries(e.target.value)}
                        >
                            <option value="">All Series</option>
                            {uniqueSeries.map(series => (
                                <option key={series} value={series}>{series}</option>
                            ))}
                        </select>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'flex-end' }}>
                        <button
                            className="btn btn-secondary"
                            onClick={() => {
                                setSearchTerm('')
                                setSelectedAuthor('')
                                setSelectedSeries('')
                            }}
                            disabled={!searchTerm && !selectedAuthor && !selectedSeries}
                        >
                            Clear
                        </button>
                    </div>
                </div>
            </div>

            {/* EBooks Tab */}
            {activeTab === 'ebooks' && (
                <div className="card" style={{ marginBottom: '24px' }}>
                    <div className="card-header">
                        <h3>📚 EBooks ({filteredEbooks.length})</h3>
                    </div>

                    {filteredEbooks.length === 0 ? (
                        <div className="empty-state">
                            <div className="icon">📖</div>
                            <h3>No ebooks found</h3>
                            <p>{ebooks.length === 0 ? "Scan your directories or upload an ebook to get started." : "Try adjusting your filters."}</p>
                        </div>
                    ) : (
                        <div className="table-wrapper">
                            <table>
                                <thead>
                                    <tr>
                                        {selectMode && (
                                            <th style={{ width: '36px' }}>
                                                <input
                                                    type="checkbox"
                                                    checked={filteredEbooks.length > 0 && filteredEbooks.every(b => selectedIds.has(b.id))}
                                                    onChange={() => toggleSelectAll(filteredEbooks)}
                                                />
                                            </th>
                                        )}
                                        <SortableHeader label="Title" column="title" sortCol={ebookSort.col} sortDir={ebookSort.dir} onSort={handleEbookSort} />
                                        <SortableHeader label="Author" column="author" sortCol={ebookSort.col} sortDir={ebookSort.dir} onSort={handleEbookSort} />
                                        <SortableHeader label="Series" column="series" sortCol={ebookSort.col} sortDir={ebookSort.dir} onSort={handleEbookSort} />
                                        <SortableHeader label="Format" column="format" sortCol={ebookSort.col} sortDir={ebookSort.dir} onSort={handleEbookSort} />
                                        <SortableHeader label="Size" column="file_size" sortCol={ebookSort.col} sortDir={ebookSort.dir} onSort={handleEbookSort} />
                                        <SortableHeader label="Added" column="uploaded_at" sortCol={ebookSort.col} sortDir={ebookSort.dir} onSort={handleEbookSort} />
                                        {!selectMode && <th style={{ width: '60px' }}></th>}
                                    </tr>
                                </thead>
                                <tbody>
                                    {filteredEbooks.map(book => (
                                        <tr
                                            key={book.id}
                                            style={selectMode && selectedIds.has(book.id) ? { background: 'rgba(139,92,246,0.12)' } : {}}
                                            onClick={selectMode ? () => toggleSelect(book.id) : undefined}
                                        >
                                            {selectMode && (
                                                <td onClick={e => e.stopPropagation()}>
                                                    <input type="checkbox" checked={selectedIds.has(book.id)} onChange={() => toggleSelect(book.id)} />
                                                </td>
                                            )}
                                            <td style={{ fontWeight: 500 }}>
                                                {selectMode ? (
                                                    <span style={{ cursor: 'pointer' }}>{book.title}</span>
                                                ) : (
                                                    <Link to={`/book/ebook/${book.id}`} style={{ color: 'var(--accent)', textDecoration: 'none' }}>
                                                        {book.title}
                                                    </Link>
                                                )}
                                            </td>
                                            <td style={{ color: 'var(--text-secondary)' }}>{book.author || '—'}</td>
                                            <td style={{ color: 'var(--text-secondary)' }}>
                                                {book.series ? `${book.series}${book.series_index ? ` #${book.series_index}` : ''}` : '—'}
                                            </td>
                                            <td><span className="badge badge-auto_matched">{book.format}</span></td>
                                            <td style={{ color: 'var(--text-muted)' }}>{formatSize(book.file_size)}</td>
                                            <td style={{ color: 'var(--text-muted)' }}>{new Date(book.uploaded_at).toLocaleDateString()}</td>
                                            {!selectMode && (
                                                <td style={{ whiteSpace: 'nowrap' }}>
                                                    <button
                                                        className="btn btn-sm btn-secondary"
                                                        onClick={() => openEdit(book, 'ebook')}
                                                        title="Edit metadata"
                                                        style={{ padding: '4px 8px', fontSize: '0.8rem', marginRight: '4px' }}
                                                    >
                                                        ✏️
                                                    </button>
                                                    <button
                                                        className="btn btn-sm btn-danger"
                                                        onClick={() => openDeleteModal(book, 'ebook')}
                                                        title="Delete ebook"
                                                        style={{ padding: '4px 8px', fontSize: '0.8rem' }}
                                                    >
                                                        🗑️
                                                    </button>
                                                </td>
                                            )}
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </div>
            )
            }

            {/* Audiobooks Tab */}
            {
                activeTab === 'audiobooks' && (
                    <div className="card">
                        <div className="card-header">
                            <h3>🎧 Audiobooks ({filteredAudiobooks.length})</h3>
                        </div>
                        {filteredAudiobooks.length === 0 ? (
                            <div className="empty-state">
                                <div className="icon">🎵</div>
                                <h3>No audiobooks found</h3>
                                <p>{audiobooks.length === 0 ? "Scan your directories or upload an audiobook to get started." : "Try adjusting your filters."}</p>
                            </div>
                        ) : (
                            <div className="table-wrapper">
                                <table>
                                    <thead>
                                        <tr>
                                            {selectMode && (
                                                <th style={{ width: '36px' }}>
                                                    <input
                                                        type="checkbox"
                                                        checked={filteredAudiobooks.length > 0 && filteredAudiobooks.every(b => selectedIds.has(b.id))}
                                                        onChange={() => toggleSelectAll(filteredAudiobooks)}
                                                    />
                                                </th>
                                            )}
                                            <SortableHeader label="Title" column="title" sortCol={audiobookSort.col} sortDir={audiobookSort.dir} onSort={handleAudiobookSort} />
                                            <SortableHeader label="Author" column="author" sortCol={audiobookSort.col} sortDir={audiobookSort.dir} onSort={handleAudiobookSort} />
                                            <SortableHeader label="Series" column="series" sortCol={audiobookSort.col} sortDir={audiobookSort.dir} onSort={handleAudiobookSort} />
                                            <SortableHeader label="Format" column="format" sortCol={audiobookSort.col} sortDir={audiobookSort.dir} onSort={handleAudiobookSort} />
                                            <SortableHeader label="Size" column="file_size" sortCol={audiobookSort.col} sortDir={audiobookSort.dir} onSort={handleAudiobookSort} />
                                            <SortableHeader label="Added" column="uploaded_at" sortCol={audiobookSort.col} sortDir={audiobookSort.dir} onSort={handleAudiobookSort} />
                                            {!selectMode && <th style={{ width: '60px' }}></th>}
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {filteredAudiobooks.map(book => (
                                            <tr
                                                key={book.id}
                                                style={selectMode && selectedIds.has(book.id) ? { background: 'rgba(139,92,246,0.12)' } : {}}
                                                onClick={selectMode ? () => toggleSelect(book.id) : undefined}
                                            >
                                                {selectMode && (
                                                    <td onClick={e => e.stopPropagation()}>
                                                        <input type="checkbox" checked={selectedIds.has(book.id)} onChange={() => toggleSelect(book.id)} />
                                                    </td>
                                                )}
                                                <td style={{ fontWeight: 500 }}>
                                                    {selectMode ? (
                                                        <span style={{ cursor: 'pointer' }}>{book.title}</span>
                                                    ) : (
                                                        <Link to={`/book/audiobook/${book.id}`} style={{ color: 'var(--accent)', textDecoration: 'none' }}>
                                                            {book.title}
                                                        </Link>
                                                    )}
                                                </td>
                                                <td style={{ color: 'var(--text-secondary)' }}>{book.author || '—'}</td>
                                                <td style={{ color: 'var(--text-secondary)' }}>
                                                    {book.series ? `${book.series}${book.series_index ? ` #${book.series_index}` : ''}` : '—'}
                                                </td>
                                                <td><span className="badge badge-auto_matched">{book.format}</span></td>
                                                <td style={{ color: 'var(--text-muted)' }}>{formatSize(book.file_size)}</td>
                                                <td style={{ color: 'var(--text-muted)' }}>{new Date(book.uploaded_at).toLocaleDateString()}</td>
                                                {!selectMode && (
                                                    <td style={{ whiteSpace: 'nowrap' }}>
                                                        <button
                                                            className="btn btn-sm btn-secondary"
                                                            onClick={() => openEdit(book, 'audiobook')}
                                                            title="Edit metadata"
                                                            style={{ padding: '4px 8px', fontSize: '0.8rem', marginRight: '4px' }}
                                                        >
                                                            ✏️
                                                        </button>
                                                        <button
                                                            className="btn btn-sm btn-danger"
                                                            onClick={() => openDeleteModal(book, 'audiobook')}
                                                            title="Delete audiobook"
                                                            style={{ padding: '4px 8px', fontSize: '0.8rem' }}
                                                        >
                                                            🗑️
                                                        </button>
                                                    </td>
                                                )}
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )
                        }
                    </div >
                )
            }
            {/* Delete Confirmation Modal */}
            {deleteTarget && (
                <div style={{
                    position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
                    background: 'rgba(0,0,0,0.6)', display: 'flex',
                    alignItems: 'center', justifyContent: 'center', zIndex: 1000
                }}>
                    <div className="card" style={{ padding: '24px', maxWidth: '480px', width: '90%' }}>
                        <h3 style={{ marginTop: 0 }}>🗑️ Confirm Delete</h3>
                        <p>
                            Are you sure you want to delete <strong>{deleteTarget.title}</strong>
                            {deleteTarget.author ? ` by ${deleteTarget.author}` : ''}
                            {' '}from BookSync?
                        </p>
                        <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>
                            This will also remove any associated book pairs, sync maps, and bookmarks.
                        </p>
                        <label style={{ display: 'flex', alignItems: 'center', gap: '8px', margin: '16px 0', cursor: 'pointer' }}>
                            <input
                                type="checkbox"
                                checked={deleteSourceFile}
                                onChange={(e) => setDeleteSourceFile(e.target.checked)}
                            />
                            <span>Also delete the source file from disk</span>
                        </label>
                        {deleteSourceFile && (
                            <div className="alert alert-error" style={{ fontSize: '0.85rem', marginBottom: '16px' }}>
                                ⚠️ This will permanently delete the file from your server's filesystem!
                            </div>
                        )}
                        <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
                            <button className="btn btn-secondary" onClick={() => setDeleteTarget(null)} disabled={deleting}>
                                Cancel
                            </button>
                            <button className="btn btn-danger" onClick={handleDelete} disabled={deleting}>
                                {deleting ? <><div className="spinner"></div> Deleting...</> : 'Delete'}
                            </button>
                        </div>
                    </div>
                </div>
            )}
            {/* Edit Metadata Modal */}
            {editingBook && (
                <EnhancedMetadataModal
                    book={editingBook}
                    type={editingType}
                    onClose={() => { setEditingBook(null); setEditingType(null) }}
                    onSave={handleSaveMetadata}
                />
            )}

            {/* Floating bulk action bar */}
            {selectMode && selectedIds.size > 0 && (
                <div style={{
                    position: 'fixed', bottom: '24px', left: '50%', transform: 'translateX(-50%)',
                    background: 'var(--bg-secondary)', border: '1px solid var(--border)',
                    borderRadius: '12px', padding: '12px 20px',
                    display: 'flex', alignItems: 'center', gap: '12px',
                    boxShadow: '0 8px 32px rgba(0,0,0,0.7)', zIndex: 100,
                    whiteSpace: 'nowrap'
                }}>
                    <span style={{ fontWeight: 600, color: 'var(--text-secondary)' }}>
                        {selectedIds.size} selected
                    </span>
                    <button className="btn btn-secondary" onClick={() => setBulkEditOpen(true)}>
                        ✏️ Edit Metadata
                    </button>
                    <button
                        className="btn btn-secondary"
                        onClick={() => setBulkMatchOpen(true)}
                        title="Cycle through each selected book and search an online metadata provider to fill in missing info (title, author, cover, etc.)"
                    >
                        🔍 Bulk Match
                    </button>
                    <button className="btn btn-danger" onClick={handleBulkDelete}>
                        🗑️ Delete
                    </button>
                </div>
            )}

            {/* Bulk Edit Modal */}
            {bulkEditOpen && (
                <div style={{
                    position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
                    background: 'rgba(0,0,0,0.6)', display: 'flex',
                    alignItems: 'center', justifyContent: 'center', zIndex: 1000
                }}>
                    <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: '12px', padding: '24px', maxWidth: '520px', width: '90%', boxShadow: '0 8px 32px rgba(0,0,0,0.6)' }}>
                        <h3 style={{ marginTop: 0 }}>
                            ✏️ Edit {selectedIds.size} Book{selectedIds.size !== 1 ? 's' : ''}
                        </h3>
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
                                    <label style={{ display: 'block', marginBottom: '4px', fontSize: '0.9rem', fontWeight: 500 }}>
                                        {label}
                                    </label>
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
                            <button
                                className="btn btn-secondary"
                                onClick={() => setBulkEditOpen(false)}
                                disabled={bulkSaving}
                            >
                                Cancel
                            </button>
                            <button
                                className="btn btn-primary"
                                onClick={handleBulkEdit}
                                disabled={bulkSaving || Object.values(bulkEditFields).every(v => !v.trim())}
                            >
                                {bulkSaving
                                    ? <><div className="spinner"></div> Saving...</>
                                    : `Save to ${selectedIds.size} Book${selectedIds.size !== 1 ? 's' : ''}`}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* Bulk Match Modal */}
            {bulkMatchOpen && (
                <BulkMatchModal
                    books={(activeTab === 'ebooks' ? filteredEbooks : filteredAudiobooks).filter(b => selectedIds.has(b.id))}
                    bookType={activeTab === 'ebooks' ? 'ebook' : 'audiobook'}
                    onClose={() => setBulkMatchOpen(false)}
                    onUpdate={(id, patch) => {
                        if (activeTab === 'ebooks') setEbooks(prev => prev.map(b => b.id === id ? { ...b, ...patch } : b))
                        else setAudiobooks(prev => prev.map(b => b.id === id ? { ...b, ...patch } : b))
                    }}
                />
            )}
        </div >
    )
}

export default LibraryPage
