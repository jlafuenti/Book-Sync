import React, { useState, useEffect, useRef, useCallback } from 'react'
import { Link } from 'react-router-dom'
import {
    getLibraryIssues, startLibraryScan, getLibraryScanProgress, cancelLibraryScan,
    bulkDeleteIssues, replaceLibraryFile, requeuePair, dismissFailedAcsm,
    deleteEbook, deleteAudiobook, convertUnsupportedFile,
    rescanBook, deleteOrphanCovers,
    getEbook, getAudiobook, updateEbookMetadata, updateAudiobookMetadata,
} from '../api'
import { useAuth } from '../contexts/AuthContext'
import EnhancedMetadataModal from '../components/EnhancedMetadataModal'
import './TroubleshootPage.css'

/* ── Category metadata ─────────────────────────────────────────────── */
// `kind: 'item'` → ebook/audiobook rows that support select+bulk-delete & replace.
const CATEGORIES = [
    { key: 'audio_corrupt', label: 'Corrupt audiobooks', kind: 'item', tone: 'error' },
    { key: 'ebook_drm', label: 'DRM-encrypted ebooks', kind: 'item', tone: 'error' },
    { key: 'ebook_unreadable', label: 'Unreadable ebooks', kind: 'item', tone: 'error' },
    { key: 'missing', label: 'Missing files (not on storage)', kind: 'item', tone: 'error' },
    { key: 'zero_byte', label: 'Zero-byte / tiny files', kind: 'item', tone: 'warning' },
    { key: 'unsupported_format', label: 'Unsupported formats (MOBI/AZW3)', kind: 'unsupported', tone: 'warning' },
    { key: 'sync_map_missing', label: 'Synced pairs missing a sync map', kind: 'transcription', tone: 'warning' },
    { key: 'duplicate', label: 'Duplicate files', kind: 'dup', tone: 'warning' },
    { key: 'missing_cover', label: 'Missing covers', kind: 'cover', tone: 'warning' },
    { key: 'orphaned_cover', label: 'Orphaned cover files', kind: 'orphan', tone: 'warning' },
    { key: 'failed_transcription', label: 'Failed transcriptions', kind: 'transcription', tone: 'warning' },
    { key: 'failed_acsm', label: 'Failed ACSM imports', kind: 'acsm', tone: 'warning' },
]

function fmtBytes(bytes) {
    if (bytes == null) return '—'
    if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`
    if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(1)} MB`
    if (bytes >= 1e3) return `${(bytes / 1e3).toFixed(1)} KB`
    return `${bytes} B`
}

function Chevron({ open }) {
    return (
        <svg className={`ts-chevron${open ? ' open' : ''}`} viewBox="0 0 24 24" fill="none"
            stroke="currentColor" strokeWidth="2.5" width="16" height="16">
            <polyline points="6 9 12 15 18 9" />
        </svg>
    )
}

/* ── A single issue section (collapsible + bulk select) ────────────── */
function IssueSection({ cat, rows, canEdit, onChanged, onOpenDetails }) {
    const [open, setOpen] = useState(false)
    const [selected, setSelected] = useState(new Set())
    const [busy, setBusy] = useState(false)
    const [msg, setMsg] = useState(null)
    const [confirmDelete, setConfirmDelete] = useState(false)
    const lastIdxRef = useRef(null)
    const replaceInputRef = useRef(null)
    const replaceTargetRef = useRef(null)

    // Reset selection whenever the underlying rows change.
    useEffect(() => { setSelected(new Set()); lastIdxRef.current = null }, [rows])

    const selectable = ['item', 'unsupported', 'dup', 'orphan'].includes(cat.kind)
    const rowKey = (r, i) => `${r.item_type || cat.key}-${r.item_id ?? r.pair_id ?? r.filename ?? i}`

    const toggleRow = (i, shiftKey) => {
        setSelected(prev => {
            const next = new Set(prev)
            if (shiftKey && lastIdxRef.current !== null) {
                const [a, b] = [Math.min(lastIdxRef.current, i), Math.max(lastIdxRef.current, i)]
                for (let k = a; k <= b; k++) next.add(rowKey(rows[k], k))
            } else {
                const key = rowKey(rows[i], i)
                next.has(key) ? next.delete(key) : next.add(key)
                lastIdxRef.current = i
            }
            return next
        })
    }

    const toggleAll = () => {
        setSelected(prev => prev.size === rows.length ? new Set() : new Set(rows.map((r, i) => rowKey(r, i))))
        lastIdxRef.current = null
    }

    const selectedItems = rows.filter((r, i) => selected.has(rowKey(r, i)))

    const doBulkDelete = async () => {
        setBusy(true); setMsg(null); setConfirmDelete(false)
        try {
            if (cat.kind === 'orphan') {
                await deleteOrphanCovers(selectedItems.map(r => r.filename))
            } else {
                await bulkDeleteIssues(selectedItems.map(r => ({ item_type: r.item_type, item_id: r.item_id })))
            }
            onChanged()
        } catch (e) { setMsg({ type: 'error', text: e.message }) }
        finally { setBusy(false) }
    }

    const doRescan = async (r) => {
        setBusy(true); setMsg(null)
        try { await rescanBook(r.item_type, r.item_id); setMsg({ type: 'success', text: 'Rescanned' }); onChanged() }
        catch (e) { setMsg({ type: 'error', text: e.message }) }
        finally { setBusy(false) }
    }

    const doDeleteOrphan = async (r) => {
        setBusy(true); setMsg(null)
        try { await deleteOrphanCovers([r.filename]); onChanged() }
        catch (e) { setMsg({ type: 'error', text: e.message }) }
        finally { setBusy(false) }
    }

    const doDeleteOne = async (r) => {
        setBusy(true); setMsg(null)
        try {
            if (r.item_type === 'ebook') await deleteEbook(r.item_id, true)
            else await deleteAudiobook(r.item_id, true)
            onChanged()
        } catch (e) { setMsg({ type: 'error', text: e.message }) }
        finally { setBusy(false) }
    }

    const openReplace = (r) => { replaceTargetRef.current = r; replaceInputRef.current?.click() }
    const onReplacePicked = async (e) => {
        const file = e.target.files?.[0]; e.target.value = ''
        const r = replaceTargetRef.current
        if (!file || !r) return
        setBusy(true); setMsg(null)
        try {
            const res = await replaceLibraryFile(r.item_type, r.item_id, file)
            setMsg(res.integrity_ok
                ? { type: 'success', text: `Replaced — integrity OK` }
                : { type: 'error', text: `Replaced but still failing: ${res.detail}` })
            onChanged()
        } catch (err) { setMsg({ type: 'error', text: err.message }) }
        finally { setBusy(false) }
    }

    const doConvert = async (r) => {
        setBusy(true); setMsg(null)
        try { await convertUnsupportedFile(r.item_id, false); onChanged() }
        catch (e) { setMsg({ type: 'error', text: e.message }) }
        finally { setBusy(false) }
    }

    const doRequeue = async (r) => {
        setBusy(true); setMsg(null)
        try { await requeuePair(r.pair_id); setMsg({ type: 'success', text: 'Re-queued' }); onChanged() }
        catch (e) { setMsg({ type: 'error', text: e.message }) }
        finally { setBusy(false) }
    }

    const doDismiss = async (r) => {
        setBusy(true); setMsg(null)
        try { await dismissFailedAcsm(r.filename); onChanged() }
        catch (e) { setMsg({ type: 'error', text: e.message }) }
        finally { setBusy(false) }
    }

    return (
        <div className="system-card ts-section">
            <div className="system-card-header system-card-header-clickable" onClick={() => setOpen(o => !o)}>
                <h3>
                    <span className={`ts-count-badge ${cat.tone}`}>{rows.length}</span>
                    {cat.label}
                </h3>
                <Chevron open={open} />
            </div>
            {open && (
                <div className="system-card-body">
                    {msg && <div className={`alert alert-${msg.type}`} style={{ marginBottom: 12 }}>{msg.text}</div>}

                    {canEdit && selectable && selected.size > 0 && (
                        <div className="ts-bulk-bar">
                            <span>{selected.size} selected</span>
                            <button className="btn btn-sm btn-danger" disabled={busy}
                                onClick={() => setConfirmDelete(true)}>Delete Selected</button>
                            <button className="btn btn-sm btn-secondary" disabled={busy}
                                onClick={() => { setSelected(new Set()); lastIdxRef.current = null }}>Clear</button>
                        </div>
                    )}

                    <table className="data-table">
                        <thead>
                            <tr>
                                {canEdit && selectable && (
                                    <th style={{ width: 32 }}>
                                        <input type="checkbox" checked={rows.length > 0 && selected.size === rows.length}
                                            onChange={toggleAll} title="Select all" />
                                    </th>
                                )}
                                <th>Title</th>
                                <th>Detail</th>
                                <th style={{ width: 100 }}>Size</th>
                                {canEdit && <th style={{ width: 230 }}>Actions</th>}
                            </tr>
                        </thead>
                        <tbody>
                            {rows.map((r, i) => (
                                <tr key={rowKey(r, i)}>
                                    {canEdit && selectable && (
                                        <td>
                                            <input type="checkbox" checked={selected.has(rowKey(r, i))}
                                                onClick={(e) => toggleRow(i, e.shiftKey)}
                                                onChange={() => { }} />
                                        </td>
                                    )}
                                    <td>
                                        {(() => {
                                            const title = r.title || r.filename || `Item ${r.item_id}`
                                            const target = (r.item_type && r.item_id) ? [r.item_type, r.item_id]
                                                : r.ebook_id ? ['ebook', r.ebook_id]
                                                    : r.audiobook_id ? ['audiobook', r.audiobook_id] : null
                                            return target
                                                ? <button className="ts-title-link" onClick={() => onOpenDetails(target[0], target[1])}>{title}</button>
                                                : <div style={{ fontWeight: 500 }}>{title}</div>
                                        })()}
                                        {r.author && <div className="ts-sub">{r.author}</div>}
                                        {r.file_path && <div className="ts-path">{r.file_path}</div>}
                                    </td>
                                    <td className="ts-detail">{r.detail}</td>
                                    <td style={{ color: 'var(--text-secondary)' }}>{fmtBytes(r.file_size)}</td>
                                    {canEdit && (
                                        <td>
                                            <div className="ts-row-actions">
                                                {(cat.kind === 'item') && (
                                                    <>
                                                        <button className="btn btn-sm btn-secondary" disabled={busy}
                                                            onClick={() => openReplace(r)}>Replace</button>
                                                        <button className="btn btn-sm btn-danger" disabled={busy}
                                                            onClick={() => doDeleteOne(r)}>Delete</button>
                                                    </>
                                                )}
                                                {cat.kind === 'unsupported' && (
                                                    <>
                                                        <button className="btn btn-sm btn-primary" disabled={busy}
                                                            onClick={() => doConvert(r)}>Convert</button>
                                                        <button className="btn btn-sm btn-danger" disabled={busy}
                                                            onClick={() => doDeleteOne(r)}>Delete</button>
                                                    </>
                                                )}
                                                {cat.kind === 'dup' && (
                                                    <button className="btn btn-sm btn-danger" disabled={busy}
                                                        onClick={() => doDeleteOne(r)}>Delete</button>
                                                )}
                                                {cat.kind === 'cover' && (
                                                    <button className="btn btn-sm btn-primary" disabled={busy}
                                                        onClick={() => doRescan(r)}>Rescan</button>
                                                )}
                                                {cat.kind === 'orphan' && (
                                                    <button className="btn btn-sm btn-danger" disabled={busy}
                                                        onClick={() => doDeleteOrphan(r)}>Delete</button>
                                                )}
                                                {cat.kind === 'transcription' && (
                                                    <button className="btn btn-sm btn-primary" disabled={busy}
                                                        onClick={() => doRequeue(r)}>Re-queue</button>
                                                )}
                                                {cat.kind === 'acsm' && (
                                                    <button className="btn btn-sm btn-danger" disabled={busy}
                                                        onClick={() => doDismiss(r)}>Dismiss</button>
                                                )}
                                            </div>
                                        </td>
                                    )}
                                </tr>
                            ))}
                        </tbody>
                    </table>

                    <input ref={replaceInputRef} type="file" style={{ display: 'none' }} onChange={onReplacePicked} />

                    {confirmDelete && (
                        <div className="modal-overlay" onClick={() => setConfirmDelete(false)}>
                            <div className="modal-dialog" onClick={e => e.stopPropagation()}>
                                <h3 style={{ marginTop: 0 }}>Delete {selected.size} item{selected.size !== 1 ? 's' : ''}?</h3>
                                <p>The selected files will be permanently deleted from storage and unpaired. This cannot be undone.</p>
                                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                                    <button className="btn btn-secondary" onClick={() => setConfirmDelete(false)}>Cancel</button>
                                    <button className="btn btn-danger" onClick={doBulkDelete}>Delete</button>
                                </div>
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    )
}

/* ── Page ──────────────────────────────────────────────────────────── */
function TroubleshootPage() {
    const { hasMinRole } = useAuth()
    const canEdit = hasMinRole('editor')

    const [data, setData] = useState(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)
    const [scan, setScan] = useState(null)
    const [editBook, setEditBook] = useState(null)
    const [editType, setEditType] = useState(null)
    const pollRef = useRef(null)

    const load = useCallback(async () => {
        try { setData(await getLibraryIssues()) }
        catch (e) { setError(e.message) }
        finally { setLoading(false) }
    }, [])

    const openDetails = useCallback(async (itemType, itemId) => {
        try {
            const full = itemType === 'ebook' ? await getEbook(itemId) : await getAudiobook(itemId)
            setEditType(itemType)
            setEditBook(full)
        } catch (e) { setError(`Could not load details: ${e.message}`) }
    }, [])

    const handleSaveMeta = async (bookId, meta) => {
        try {
            if (editType === 'ebook') await updateEbookMetadata(bookId, meta)
            else await updateAudiobookMetadata(bookId, meta)
            setEditBook(null); setEditType(null)
            await load()
        } catch (e) { alert('Failed to update metadata: ' + e.message) }
    }

    useEffect(() => {
        load()
        // If a scan is already running (e.g. navigated away & back), resume polling.
        getLibraryScanProgress().then(p => { if (p.running) beginPolling() }).catch(() => { })
        return () => { if (pollRef.current) clearInterval(pollRef.current) }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [load])

    const beginPolling = () => {
        if (pollRef.current) clearInterval(pollRef.current)
        pollRef.current = setInterval(async () => {
            try {
                const p = await getLibraryScanProgress()
                setScan(p)
                if (!p.running) {
                    clearInterval(pollRef.current); pollRef.current = null
                    await load()
                }
            } catch { /* keep polling */ }
        }, 1000)
    }

    const runScan = async () => {
        setError(null)
        try {
            await startLibraryScan()
            setScan({ running: true, phase_index: 0, phase_count: 3, phase_label: 'Starting…', current: 0, total: 0 })
            beginPolling()
        } catch (e) { setError(e.message) }
    }

    const stopScan = async () => { try { await cancelLibraryScan() } catch { /* ignore */ } }

    const scanning = scan?.running
    const pct = scan && scan.total > 0 ? Math.round((scan.current / scan.total) * 100) : 0

    const counts = data?.counts || {}
    const total = data?.total || 0
    const activeCats = CATEGORIES.filter(c => (data?.categories?.[c.key]?.length || 0) > 0)

    return (
        <div>
            <div style={{ marginBottom: 16 }}>
                <Link to="/system/status" className="btn btn-secondary" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="14" height="14">
                        <polyline points="15 18 9 12 15 6" />
                    </svg>
                    Back to System
                </Link>
            </div>

            <div className="system-section-header">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="18" height="18" className="system-section-icon">
                    <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
                    <line x1="12" y1="9" x2="12" y2="13" /><line x1="12" y1="17" x2="12.01" y2="17" />
                </svg>
                <h3>Troubleshoot Library</h3>
            </div>

            <div className="ts-toolbar">
                <div className="ts-summary">
                    {loading ? 'Loading…' : total === 0 ? 'No issues detected 🎉' : `${total} issue${total !== 1 ? 's' : ''} across ${activeCats.length} categor${activeCats.length !== 1 ? 'ies' : 'y'}`}
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn-secondary" onClick={load} disabled={loading || scanning}>Refresh</button>
                    {canEdit && (scanning
                        ? <button className="btn btn-danger" onClick={stopScan}>Cancel Scan</button>
                        : <button className="btn btn-primary" onClick={runScan}>Run Verification Scan</button>)}
                </div>
            </div>

            {scanning && (
                <div className="system-card ts-progress">
                    <div className="ts-progress-head">
                        <span><strong>{scan.phase_label}</strong> — phase {Math.min(scan.phase_index + 1, scan.phase_count)} of {scan.phase_count}</span>
                        <span>{scan.current} / {scan.total} ({pct}%)</span>
                    </div>
                    <div className="progress-bar" style={{ height: 8 }}>
                        <div className="progress-fill" style={{ width: `${pct}%`, background: 'var(--accent)' }} />
                    </div>
                </div>
            )}

            {error && <div className="alert alert-error" style={{ marginBottom: 16 }}>{error}</div>}

            {!loading && total === 0 && !scanning && (
                <div className="system-card"><div className="system-card-body" style={{ textAlign: 'center', color: 'var(--text-muted)', padding: 32 }}>
                    Your library is healthy. Run a verification scan to deep-check audio and ebook integrity.
                </div></div>
            )}

            {activeCats.map(cat => (
                <IssueSection key={cat.key} cat={cat} rows={data.categories[cat.key]}
                    canEdit={canEdit} onChanged={load} onOpenDetails={openDetails} />
            ))}

            {editBook && (
                <EnhancedMetadataModal
                    book={editBook}
                    type={editType}
                    onClose={() => { setEditBook(null); setEditType(null); load() }}
                    onSave={handleSaveMeta}
                />
            )}
        </div>
    )
}

export default TroubleshootPage
