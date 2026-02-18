import React, { useState, useEffect, useRef } from 'react'
import { getEbooks, getAudiobooks, uploadEbook, uploadAudiobook, scanLibrary } from '../api'

function LibraryPage() {
    const [ebooks, setEbooks] = useState([])
    const [audiobooks, setAudiobooks] = useState([])
    const [loading, setLoading] = useState(true)
    const [scanning, setScanning] = useState(false)
    const [scanResult, setScanResult] = useState(null)
    const [error, setError] = useState('')
    const [uploadingEbook, setUploadingEbook] = useState(false)
    const [uploadingAudiobook, setUploadingAudiobook] = useState(false)

    // Filtering state
    const [searchTerm, setSearchTerm] = useState('')
    const [selectedAuthor, setSelectedAuthor] = useState('')
    const [selectedSeries, setSelectedSeries] = useState('')

    const ebookFileRef = useRef(null)
    const audiobookFileRef = useRef(null)

    // Compute unique authors and series for filters
    const allBooks = [...ebooks, ...audiobooks]
    const uniqueAuthors = [...new Set(allBooks.map(b => b.author).filter(Boolean))].sort()
    const uniqueSeries = [...new Set(allBooks.map(b => b.series).filter(Boolean))].sort()

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

    const filteredEbooks = ebooks.filter(filterBook)
    const filteredAudiobooks = audiobooks.filter(filterBook)

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
                <button className="btn btn-primary" onClick={handleScan} disabled={scanning}>
                    {scanning ? <><div className="spinner"></div> Scanning...</> : '🔍 Scan Directories'}
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

            {/* EBooks Table */}
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
                                    <th>Title</th>
                                    <th>Author</th>
                                    <th>Series</th>
                                    <th>Format</th>
                                    <th>Size</th>
                                    <th>Added</th>
                                </tr>
                            </thead>
                            <tbody>
                                {filteredEbooks.map(book => (
                                    <tr key={book.id}>
                                        <td style={{ fontWeight: 500 }}>{book.title}</td>
                                        <td style={{ color: 'var(--text-secondary)' }}>{book.author || '—'}</td>
                                        <td style={{ color: 'var(--text-secondary)' }}>
                                            {book.series ? `${book.series} #${book.series_index}` : '—'}
                                        </td>
                                        <td><span className="badge badge-auto_matched">{book.format}</span></td>
                                        <td style={{ color: 'var(--text-muted)' }}>{formatSize(book.file_size)}</td>
                                        <td style={{ color: 'var(--text-muted)' }}>{new Date(book.uploaded_at).toLocaleDateString()}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>

            {/* Audiobooks Table */}
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
                                    <th>Title</th>
                                    <th>Author</th>
                                    <th>Series</th>
                                    <th>Format</th>
                                    <th>Size</th>
                                    <th>Added</th>
                                </tr>
                            </thead>
                            <tbody>
                                {filteredAudiobooks.map(book => (
                                    <tr key={book.id}>
                                        <td style={{ fontWeight: 500 }}>{book.title}</td>
                                        <td style={{ color: 'var(--text-secondary)' }}>{book.author || '—'}</td>
                                        <td style={{ color: 'var(--text-secondary)' }}>
                                            {book.series ? `${book.series} #${book.series_index}` : '—'}
                                        </td>
                                        <td><span className="badge badge-auto_matched">{book.format}</span></td>
                                        <td style={{ color: 'var(--text-muted)' }}>{formatSize(book.file_size)}</td>
                                        <td style={{ color: 'var(--text-muted)' }}>{new Date(book.uploaded_at).toLocaleDateString()}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>
        </div>
    )
}

export default LibraryPage
