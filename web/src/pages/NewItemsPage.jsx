import React, { useState, useEffect, useCallback, useRef } from 'react'
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

    // Collapsible sections
    const [ebooksCollapsed, setEbooksCollapsed] = useState(false)
    const [audiobooksCollapsed, setAudiobooksCollapsed] = useState(false)

    // Metadata edit modal
    const [editModal, setEditModal] = useState(null)  // { book, type }

    // Refs for jump-to scroll
    const ebooksRef = useRef(null)
    const audiobooksRef = useRef(null)

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
        if (allSelected) setSelected(new Set())
        else setSelected(new Set(allKeys))
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
        await acknowledgeNewItems(ebooks.map(e => e.id), audiobooks.map(a => a.id))
        await load()
    }

    function jumpTo(ref, collapsed, setCollapsed) {
        if (collapsed) setCollapsed(false)
        // Small delay so section expands before scrolling
        setTimeout(() => ref.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50)
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

    function BookTable({ items, type }) {
        return (
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
                        </tr>
                    </thead>
                    <tbody>
                        {items.map(book => (
                            <tr
                                key={book.id}
                                className={selected.has(`${type}:${book.id}`) ? 'row-selected' : ''}
                            >
                                <td>
                                    <input
                                        type="checkbox"
                                        checked={selected.has(`${type}:${book.id}`)}
                                        onChange={() => toggle(type, book.id)}
                                    />
                                </td>
                                <td>
                                    {canEdit ? (
                                        <button
                                            style={{
                                                background: 'none', border: 'none', padding: 0,
                                                color: 'var(--accent)', cursor: 'pointer',
                                                textAlign: 'left', fontWeight: 500, fontSize: 'inherit',
                                            }}
                                            onClick={() => setEditModal({ book, type })}
                                            title="Click to edit metadata"
                                        >
                                            {book.title}
                                        </button>
                                    ) : book.title}
                                </td>
                                <td>{book.author || '—'}</td>
                                <td>{book.series ? `${book.series}${book.series_index ? ` #${book.series_index}` : ''}` : '—'}</td>
                                <td>{book.format?.toUpperCase()}</td>
                                <td>{formatSize(book.file_size)}</td>
                                <td>{formatDate(book.uploaded_at)}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        )
    }

    return (
        <div className="page-content">
            <div className="page-header">
                <h2>New Items <span className="badge">{total}</span></h2>
                <p className="page-subtitle">
                    Newly added files awaiting review. Click a title to edit metadata. Acknowledge once checked, or pair to remove automatically.
                </p>
            </div>

            {total === 0 ? (
                <div className="empty-state">
                    <p>✅ No new items — you're all caught up!</p>
                </div>
            ) : (
                <>
                    {/* Toolbar: select-all, jump buttons, bulk actions */}
                    <div style={{
                        display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap',
                        position: 'sticky', top: 0, zIndex: 10,
                        background: 'var(--background, #fff)',
                        padding: '0.5rem 0',
                        marginBottom: '0.5rem',
                        borderBottom: '1px solid var(--border)',
                    }}>
                        <label style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', cursor: 'pointer' }}>
                            <input type="checkbox" checked={allSelected} onChange={toggleAll} />
                            Select all ({total})
                        </label>

                        {/* Jump buttons */}
                        {ebooks.length > 0 && audiobooks.length > 0 && (
                            <div style={{ display: 'flex', gap: '0.25rem', marginLeft: '0.5rem' }}>
                                <button
                                    className="btn btn-sm btn-secondary"
                                    onClick={() => jumpTo(ebooksRef, ebooksCollapsed, setEbooksCollapsed)}
                                    title="Jump to Ebooks section"
                                >
                                    📚 Ebooks ({ebooks.length})
                                </button>
                                <button
                                    className="btn btn-sm btn-secondary"
                                    onClick={() => jumpTo(audiobooksRef, audiobooksCollapsed, setAudiobooksCollapsed)}
                                    title="Jump to Audiobooks section"
                                >
                                    🎧 Audiobooks ({audiobooks.length})
                                </button>
                            </div>
                        )}

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
                        <section ref={ebooksRef} style={{ marginBottom: '2rem' }}>
                            <button
                                className="section-collapse-header"
                                onClick={() => setEbooksCollapsed(c => !c)}
                                style={{
                                    display: 'flex', alignItems: 'center', gap: '0.5rem',
                                    background: 'none', border: 'none', cursor: 'pointer',
                                    padding: '0.25rem 0', marginBottom: '0.5rem',
                                    color: 'var(--text)', fontSize: '1.05rem', fontWeight: 600,
                                }}
                            >
                                <span style={{ fontSize: '0.75em', color: 'var(--text-muted)' }}>
                                    {ebooksCollapsed ? '▶' : '▼'}
                                </span>
                                📚 Ebooks ({ebooks.length})
                            </button>
                            {!ebooksCollapsed && <BookTable items={ebooks} type="ebook" />}
                        </section>
                    )}

                    {/* Audiobooks */}
                    {audiobooks.length > 0 && (
                        <section ref={audiobooksRef}>
                            <button
                                className="section-collapse-header"
                                onClick={() => setAudiobooksCollapsed(c => !c)}
                                style={{
                                    display: 'flex', alignItems: 'center', gap: '0.5rem',
                                    background: 'none', border: 'none', cursor: 'pointer',
                                    padding: '0.25rem 0', marginBottom: '0.5rem',
                                    color: 'var(--text)', fontSize: '1.05rem', fontWeight: 600,
                                }}
                            >
                                <span style={{ fontSize: '0.75em', color: 'var(--text-muted)' }}>
                                    {audiobooksCollapsed ? '▶' : '▼'}
                                </span>
                                🎧 Audiobooks ({audiobooks.length})
                            </button>
                            {!audiobooksCollapsed && <BookTable items={audiobooks} type="audiobook" />}
                        </section>
                    )}
                </>
            )}

            {/* Metadata edit modal — saving or applying a match auto-acknowledges the item */}
            {editModal && (
                <EnhancedMetadataModal
                    book={editModal.book}
                    bookType={editModal.type}
                    onClose={() => setEditModal(null)}
                    onSave={async (bookId, meta) => {
                        const fn = editModal.type === 'ebook' ? updateEbookMetadata : updateAudiobookMetadata
                        await fn(bookId, meta)
                        // Auto-acknowledge: saving details (or after applying a match) clears from inbox
                        if (editModal.type === 'ebook') await acknowledgeNewItems([bookId], [])
                        else await acknowledgeNewItems([], [bookId])
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
