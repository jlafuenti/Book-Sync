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

    // Shift+click range selection: track last clicked key
    const lastClickedRef = useRef(null)

    const load = useCallback(async () => {
        try {
            setLoading(true)
            const data = await getNewItems()
            setEbooks(data.ebooks || [])
            setAudiobooks(data.audiobooks || [])
            setSelected(new Set())
            lastClickedRef.current = null
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
        lastClickedRef.current = null
    }

    function handleCheck(type, id, event) {
        const key = `${type}:${id}`
        if (event.shiftKey && lastClickedRef.current) {
            const idx1 = allKeys.indexOf(lastClickedRef.current)
            const idx2 = allKeys.indexOf(key)
            if (idx1 !== -1 && idx2 !== -1) {
                const [start, end] = [Math.min(idx1, idx2), Math.max(idx1, idx2)]
                const rangeKeys = allKeys.slice(start, end + 1)
                // If the clicked item is being checked, check the whole range; otherwise uncheck
                const shouldCheck = !selected.has(key)
                setSelected(prev => {
                    const next = new Set(prev)
                    rangeKeys.forEach(k => shouldCheck ? next.add(k) : next.delete(k))
                    return next
                })
            }
        } else {
            setSelected(prev => {
                const next = new Set(prev)
                if (next.has(key)) next.delete(key)
                else next.add(key)
                return next
            })
        }
        lastClickedRef.current = key
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
                                        onChange={(e) => handleCheck(type, book.id, e)}
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
                    {/* Inline top controls: select-all, jump buttons, acknowledge all */}
                    <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', marginBottom: '1rem', flexWrap: 'wrap' }}>
                        <label style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', cursor: 'pointer' }}>
                            <input type="checkbox" checked={allSelected} onChange={toggleAll} />
                            Select all ({total})
                        </label>

                        {ebooks.length > 0 && audiobooks.length > 0 && (
                            <div style={{ display: 'flex', gap: '0.25rem', marginLeft: '0.5rem' }}>
                                <button
                                    className="btn btn-sm btn-secondary"
                                    onClick={() => jumpTo(ebooksRef, ebooksCollapsed, setEbooksCollapsed)}
                                >
                                    📚 Ebooks ({ebooks.length})
                                </button>
                                <button
                                    className="btn btn-sm btn-secondary"
                                    onClick={() => jumpTo(audiobooksRef, audiobooksCollapsed, setAudiobooksCollapsed)}
                                >
                                    🎧 Audiobooks ({audiobooks.length})
                                </button>
                            </div>
                        )}

                        {canEdit && (
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

            {/* Floating bottom bar — appears when anything is selected */}
            {selectedCount > 0 && canEdit && (
                <div style={{
                    position: 'fixed', bottom: '24px', left: '50%', transform: 'translateX(-50%)',
                    background: 'var(--bg-secondary)', border: '1px solid var(--border)',
                    borderRadius: '12px', padding: '12px 20px',
                    display: 'flex', alignItems: 'center', gap: '12px',
                    boxShadow: '0 8px 32px rgba(0,0,0,0.7)', zIndex: 100,
                    whiteSpace: 'nowrap',
                }}>
                    <span style={{ fontWeight: 600, color: 'var(--text-secondary)' }}>
                        {selectedCount} selected
                    </span>
                    <button className="btn btn-primary" onClick={acknowledgeSelected}>
                        Acknowledge selected
                    </button>
                </div>
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
