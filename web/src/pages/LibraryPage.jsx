import React, { useState, useEffect, useRef, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { getEbooks, getAudiobooks, uploadEbook, uploadAudiobook, scanLibrary, normalizeLibrary, updateEbookMetadata, updateAudiobookMetadata } from '../api'

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

    // Filtering state
    const [searchTerm, setSearchTerm] = useState('')
    const [selectedAuthor, setSelectedAuthor] = useState('')
    const [selectedSeries, setSelectedSeries] = useState('')

    // Sort state — separate for each table
    const [ebookSort, setEbookSort] = useState({ col: null, dir: null })
    const [audiobookSort, setAudiobookSort] = useState({ col: null, dir: null })

    const ebookFileRef = useRef(null)
    const audiobookFileRef = useRef(null)

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
            <div style={{ display: 'flex', gap: '12px', marginBottom: '24px', flexWrap: 'wrap' }}>
                <button className="btn btn-primary" onClick={handleScan} disabled={scanning || normalizing}>
                    {scanning ? <><div className="spinner"></div> Scanning...</> : '🔍 Scan Directories'}
                </button>
                <button
                    className="btn btn-secondary"
                    onClick={handleNormalize}
                    disabled={scanning || normalizing}
                    title="Fix author names like 'Butcher, Jim' → 'Jim Butcher' and series like 'Dresden Files, The' → 'The Dresden Files'"
                >
                    {normalizing ? <><div className="spinner"></div> Normalizing...</> : '🔄 Normalize Metadata'}
                </button>
                <button className="btn btn-secondary" onClick={() => ebookFileRef.current?.click()} disabled={uploadingEbook}>
                    {uploadingEbook ? <><div className="spinner"></div> Uploading...</> : '📄 Upload EBook'}
                </button>
                <button className="btn btn-secondary" onClick={() => audiobookFileRef.current?.click()} disabled={uploadingAudiobook}>
                    {uploadingAudiobook ? <><div className="spinner"></div> Uploading...</> : '🎵 Upload Audiobook'}
                </button>
                <input ref={ebookFileRef} type="file" accept=".epub,.pdf,.mobi" hidden onChange={handleEbookUpload} />
                <input ref={audiobookFileRef} type="file" accept=".mp3,.m4a,.m4b,.flac,.ogg,.wav" hidden onChange={handleAudiobookUpload} />
            </div>

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
                                        <SortableHeader label="Title" column="title" sortCol={ebookSort.col} sortDir={ebookSort.dir} onSort={handleEbookSort} />
                                        <SortableHeader label="Author" column="author" sortCol={ebookSort.col} sortDir={ebookSort.dir} onSort={handleEbookSort} />
                                        <SortableHeader label="Series" column="series" sortCol={ebookSort.col} sortDir={ebookSort.dir} onSort={handleEbookSort} />
                                        <SortableHeader label="Format" column="format" sortCol={ebookSort.col} sortDir={ebookSort.dir} onSort={handleEbookSort} />
                                        <SortableHeader label="Size" column="file_size" sortCol={ebookSort.col} sortDir={ebookSort.dir} onSort={handleEbookSort} />
                                        <SortableHeader label="Added" column="uploaded_at" sortCol={ebookSort.col} sortDir={ebookSort.dir} onSort={handleEbookSort} />
                                        <th style={{ width: '60px' }}></th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {filteredEbooks.map(book => (
                                        <tr key={book.id}>
                                            <td style={{ fontWeight: 500 }}>
                                                <Link to={`/book/ebook/${book.id}`} style={{ color: 'var(--accent)', textDecoration: 'none' }}>
                                                    {book.title}
                                                </Link>
                                            </td>
                                            <td style={{ color: 'var(--text-secondary)' }}>{book.author || '—'}</td>
                                            <td style={{ color: 'var(--text-secondary)' }}>
                                                {book.series ? `${book.series}${book.series_index ? ` #${book.series_index}` : ''}` : '—'}
                                            </td>
                                            <td><span className="badge badge-auto_matched">{book.format}</span></td>
                                            <td style={{ color: 'var(--text-muted)' }}>{formatSize(book.file_size)}</td>
                                            <td style={{ color: 'var(--text-muted)' }}>{new Date(book.uploaded_at).toLocaleDateString()}</td>
                                            <td>
                                                <button
                                                    className="btn btn-sm btn-secondary"
                                                    onClick={() => openEdit(book, 'ebook')}
                                                    title="Edit metadata"
                                                    style={{ padding: '4px 8px', fontSize: '0.8rem' }}
                                                >
                                                    ✏️
                                                </button>
                                            </td>
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
                                            <SortableHeader label="Title" column="title" sortCol={audiobookSort.col} sortDir={audiobookSort.dir} onSort={handleAudiobookSort} />
                                            <SortableHeader label="Author" column="author" sortCol={audiobookSort.col} sortDir={audiobookSort.dir} onSort={handleAudiobookSort} />
                                            <SortableHeader label="Series" column="series" sortCol={audiobookSort.col} sortDir={audiobookSort.dir} onSort={handleAudiobookSort} />
                                            <SortableHeader label="Format" column="format" sortCol={audiobookSort.col} sortDir={audiobookSort.dir} onSort={handleAudiobookSort} />
                                            <SortableHeader label="Size" column="file_size" sortCol={audiobookSort.col} sortDir={audiobookSort.dir} onSort={handleAudiobookSort} />
                                            <SortableHeader label="Added" column="uploaded_at" sortCol={audiobookSort.col} sortDir={audiobookSort.dir} onSort={handleAudiobookSort} />
                                            <th style={{ width: '60px' }}></th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {filteredAudiobooks.map(book => (
                                            <tr key={book.id}>
                                                <td style={{ fontWeight: 500 }}>
                                                    <Link to={`/book/audiobook/${book.id}`} style={{ color: 'var(--accent)', textDecoration: 'none' }}>
                                                        {book.title}
                                                    </Link>
                                                </td>
                                                <td style={{ color: 'var(--text-secondary)' }}>{book.author || '—'}</td>
                                                <td style={{ color: 'var(--text-secondary)' }}>
                                                    {book.series ? `${book.series}${book.series_index ? ` #${book.series_index}` : ''}` : '—'}
                                                </td>
                                                <td><span className="badge badge-auto_matched">{book.format}</span></td>
                                                <td style={{ color: 'var(--text-muted)' }}>{formatSize(book.file_size)}</td>
                                                <td style={{ color: 'var(--text-muted)' }}>{new Date(book.uploaded_at).toLocaleDateString()}</td>
                                                <td>
                                                    <button
                                                        className="btn btn-sm btn-secondary"
                                                        onClick={() => openEdit(book, 'audiobook')}
                                                        title="Edit metadata"
                                                        style={{ padding: '4px 8px', fontSize: '0.8rem' }}
                                                    >
                                                        ✏️
                                                    </button>
                                                </td>
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
        </div >
    )
}

export default LibraryPage
