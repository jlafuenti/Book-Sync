import React, { useState, useEffect, useCallback } from 'react'
import { getNewItems, acknowledgeNewItems, uploadEbookCover, uploadAudiobookCover, updateEbookMetadata, updateAudiobookMetadata } from '../api'
import EnhancedMetadataModal from '../components/EnhancedMetadataModal'
import { useAuth } from '../contexts/AuthContext'

/**
 * New Items inbox — shows unacknowledged ebooks and audiobooks.
 * Items disappear once acknowledged or once paired.
 */
export default function NewItemsPage() {
    const { hasMinRole } = useAuth()
    const canEdit = hasMinRole('editor')

    const [ebooks, setEbooks] = useState([])
    const [audiobooks, setAudiobooks] = useState([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)

    // Multi-select: Set of "ebook:id" or "audiobook:id" strings
    const [selected, setSelected] = useState(new Set())

    // Metadata edit modal
    const [editModal, setEditModal] = useState(null)  // { book, type }

    const load = useCallback(async () => {
        try {
            setLoading(true)
            const data = await getNewItems()
            setEbooks(data.ebooks || [])
            setAudiobooks(data.audiobooks || [])
            setSelected(new Set())
        } catch (e) {
            setError(e.message)
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => { load() }, [load])

    const allKeys = [
        ...ebooks.map(e => `ebook:${e.id}`),
        ...audiobooks.map(a => `audiobook:${a.id}`),
    ]
    const allSelected = allKeys.length > 0 && allKeys.every(k => selected.has(k))

    function toggleAll() {
        if (allSelected) {
            setSelected(new Set())
        } else {
            setSelected(new Set(allKeys))
        }
    }

    function toggle(type, id) {
        const key = `${type}:${id}`
        setSelected(prev => {
            const next = new Set(prev)
            if (next.has(key)) next.delete(key)
            else next.add(key)
            return next
        })
    }

    async function acknowledgeSelected() {
        const ebookIds = [...selected].filter(k => k.startsWith('ebook:')).map(k => parseInt(k.split(':')[1]))
        const audiobookIds = [...selected].filter(k => k.startsWith('audiobook:')).map(k => parseInt(k.split(':')[1]))
        await acknowledgeNewItems(ebookIds, audiobookIds)
        await load()
    }

    async function acknowledgeAll() {
        const ebookIds = ebooks.map(e => e.id)
        const audiobookIds = audiobooks.map(a => a.id)
        await acknowledgeNewItems(ebookIds, audiobookIds)
        await load()
    }

    function formatSize(bytes) {
        if (!bytes) return '—'
        if (bytes >= 1024 * 1024 * 1024) return (bytes / (1024 * 1024 * 1024)).toFixed(1) + ' GB'
        if (bytes >= 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
        return (bytes / 1024).toFixed(0) + ' KB'
    }

    function formatDate(dt) {
        if (!dt) return '—'
        return new Date(dt).toLocaleDateString()
    }

    if (loading) return <div className="page-content"><p>Loading new items…</p></div>
    if (error) return <div className="page-content"><p className="error">{error}</p></div>

    const total = ebooks.length + audiobooks.length
    const selectedCount = selected.size

    return (
        <div className="page-content">
            <div className="page-header">
                <h2>New Items <span className="badge">{total}</span></h2>
                <p className="page-subtitle">
                    Newly added files awaiting review. Acknowledge items once you've checked them, or pair them to remove them automatically.
                </p>
            </div>

            {total === 0 ? (
                <div className="empty-state">
                    <p>✅ No new items — you're all caught up!</p>
                </div>
            ) : (
                <>
                    {/* Bulk actions */}
                    <div className="bulk-actions" style={{ marginBottom: '1rem', display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                        <label style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', cursor: 'pointer' }}>
                            <input
                                type="checkbox"
                                checked={allSelected}
                                onChange={toggleAll}
                            />
                            Select all ({total})
                        </label>
                        {selectedCount > 0 && canEdit && (
                            <button className="btn btn-secondary" onClick={acknowledgeSelected}>
                                Acknowledge selected ({selectedCount})
                            </button>
                        )}
                        {canEdit && total > 0 && (
                            <button className="btn btn-secondary" onClick={acknowledgeAll} style={{ marginLeft: 'auto' }}>
                                Acknowledge all
                            </button>
                        )}
                    </div>

                    {/* Ebooks */}
                    {ebooks.length > 0 && (
                        <section style={{ marginBottom: '2rem' }}>
                            <h3 style={{ marginBottom: '0.5rem' }}>📚 Ebooks ({ebooks.length})</h3>
                            <div className="table-wrapper">
                                <table>
                                    <thead>
                                        <tr>
                                            <th style={{ width: 36 }}></th>
                                            <th>Title</th>
                                            <th>Author</th>
                                            <th>Series</th>
                                            <th>Format</th>
                                            <th>Size</th>
                                            <th>Added</th>
                                            {canEdit && <th>Actions</th>}
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {ebooks.map(book => (
                                            <tr key={book.id} className={selected.has(`ebook:${book.id}`) ? 'row-selected' : ''}>
                                                <td>
                                                    <input
                                                        type="checkbox"
                                                        checked={selected.has(`ebook:${book.id}`)}
                                                        onChange={() => toggle('ebook', book.id)}
                                                    />
                                                </td>
                                                <td>{book.title}</td>
                                                <td>{book.author || '—'}</td>
                                                <td>{book.series ? `${book.series}${book.series_index ? ` #${book.series_index}` : ''}` : '—'}</td>
                                                <td>{book.format?.toUpperCase()}</td>
                                                <td>{formatSize(book.file_size)}</td>
                                                <td>{formatDate(book.uploaded_at)}</td>
                                                {canEdit && (
                                                    <td>
                                                        <button
                                                            className="btn btn-sm btn-secondary"
                                                            onClick={() => setEditModal({ book, type: 'ebook' })}
                                                        >
                                                            Edit
                                                        </button>
                                                    </td>
                                                )}
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </section>
                    )}

                    {/* Audiobooks */}
                    {audiobooks.length > 0 && (
                        <section>
                            <h3 style={{ marginBottom: '0.5rem' }}>🎧 Audiobooks ({audiobooks.length})</h3>
                            <div className="table-wrapper">
                                <table>
                                    <thead>
                                        <tr>
                                            <th style={{ width: 36 }}></th>
                                            <th>Title</th>
                                            <th>Author</th>
                                            <th>Series</th>
                                            <th>Format</th>
                                            <th>Size</th>
                                            <th>Added</th>
                                            {canEdit && <th>Actions</th>}
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {audiobooks.map(book => (
                                            <tr key={book.id} className={selected.has(`audiobook:${book.id}`) ? 'row-selected' : ''}>
                                                <td>
                                                    <input
                                                        type="checkbox"
                                                        checked={selected.has(`audiobook:${book.id}`)}
                                                        onChange={() => toggle('audiobook', book.id)}
                                                    />
                                                </td>
                                                <td>{book.title}</td>
                                                <td>{book.author || '—'}</td>
                                                <td>{book.series ? `${book.series}${book.series_index ? ` #${book.series_index}` : ''}` : '—'}</td>
                                                <td>{book.format?.toUpperCase()}</td>
                                                <td>{formatSize(book.file_size)}</td>
                                                <td>{formatDate(book.uploaded_at)}</td>
                                                {canEdit && (
                                                    <td>
                                                        <button
                                                            className="btn btn-sm btn-secondary"
                                                            onClick={() => setEditModal({ book, type: 'audiobook' })}
                                                        >
                                                            Edit
                                                        </button>
                                                    </td>
                                                )}
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </section>
                    )}
                </>
            )}

            {/* Metadata edit modal */}
            {editModal && (
                <EnhancedMetadataModal
                    book={editModal.book}
                    bookType={editModal.type}
                    onClose={() => setEditModal(null)}
                    onSave={async (bookId, meta) => {
                        const fn = editModal.type === 'ebook' ? updateEbookMetadata : updateAudiobookMetadata
                        await fn(bookId, meta)
                        setEditModal(null)
                        await load()
                    }}
                    onCoverUpload={async (bookId, file) => {
                        const fn = editModal.type === 'ebook' ? uploadEbookCover : uploadAudiobookCover
                        await fn(bookId, file)
                        await load()
                    }}
                />
            )}
        </div>
    )
}
