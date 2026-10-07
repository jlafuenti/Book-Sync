import { useState } from 'react'
import {
    convertUnsupportedFile, convertAllUnsupportedFiles, deleteUnsupportedSource,
    forceDeleteUnsupportedFile, forceDeleteAllUnsupportedFiles,
} from '../api'
import EbookReader from './EbookReader'
import Modal from './Modal'
import './UnsupportedFilesPanel.css'

const formatBytes = (bytes) => {
    if (!bytes) return '—'
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/**
 * MOBI/AZW3 files and what can be done with them: convert to EPUB (one or
 * all, keeping or deleting the original), delete an original already
 * converted, or force-delete. Lives in Troubleshoot Library's "Unsupported
 * formats" section; it was the System page's Unsupported Files view.
 *
 * `files` is `GET /api/library/unsupported`, loaded by the caller, which also
 * lists files already converted whose original is still on disk. `onChanged`
 * reloads it (and the page's issue counts) after anything changes.
 */
export default function UnsupportedFilesPanel({ files, canAdmin, onChanged }) {
    const [busyIds, setBusyIds] = useState(new Set())
    const [batchBusy, setBatchBusy] = useState(false)
    const [batchResult, setBatchResult] = useState(null)
    const [fileMessages, setFileMessages] = useState({})
    const [previewFile, setPreviewFile] = useState(null)
    const [forceDeleteConfirm, setForceDeleteConfirm] = useState(null) // null | { file } | 'all'

    const setFileBusy = (id, busy) => setBusyIds(prev => {
        const next = new Set(prev); busy ? next.add(id) : next.delete(id); return next
    })
    const setFileMsg = (id, msg) => setFileMessages(prev => ({ ...prev, [id]: msg }))

    const handleConvert = async (file, deleteSource) => {
        setFileBusy(file.id, true); setFileMsg(file.id, null)
        try {
            const result = await convertUnsupportedFile(file.id, deleteSource)
            // A converted file whose pairs were re-pointed also needs its sync
            // map rebuilt on the new EPUB's axis (issue #101). The conversion
            // succeeds either way, so a failure here is a warning, not an error
            // — but it must not pass silently: the pair's stored coordinates no
            // longer describe its ebook until it is transcribed or re-aligned.
            if (result?.realign_error) {
                setFileMsg(file.id, {
                    type: 'error',
                    text: `Converted, but the sync map could not be rebuilt: ${result.realign_error}`,
                })
            } else {
                setFileMsg(file.id, { type: 'success', text: deleteSource ? 'Converted & deleted' : 'Converted to EPUB' })
            }
            await onChanged()
        } catch (err) { setFileMsg(file.id, { type: 'error', text: err.message }) }
        finally { setFileBusy(file.id, false) }
    }

    const handleDeleteSource = async (file) => {
        setFileBusy(file.id, true); setFileMsg(file.id, null)
        try { await deleteUnsupportedSource(file.id); await onChanged() }
        catch (err) { setFileMsg(file.id, { type: 'error', text: err.message }) }
        finally { setFileBusy(file.id, false) }
    }

    const handleForceDelete = async (file) => {
        setFileBusy(file.id, true); setFileMsg(file.id, null); setForceDeleteConfirm(null)
        try { await forceDeleteUnsupportedFile(file.id); await onChanged() }
        catch (err) { setFileMsg(file.id, { type: 'error', text: err.message }) }
        finally { setFileBusy(file.id, false) }
    }

    const handleForceDeleteAll = async () => {
        setBatchBusy(true); setBatchResult(null); setForceDeleteConfirm(null)
        try {
            const result = await forceDeleteAllUnsupportedFiles()
            setBatchResult({ type: 'success', text: `Force deleted ${result.total} file${result.total !== 1 ? 's' : ''}.` })
            await onChanged()
        } catch (err) { setBatchResult({ type: 'error', text: err.message }) }
        finally { setBatchBusy(false) }
    }

    const handleBatchConvert = async (deleteSource) => {
        setBatchBusy(true); setBatchResult(null)
        try {
            const result = await convertAllUnsupportedFiles(deleteSource)
            const realignFailures = result.realign_failures || []
            const msg = `Converted ${result.succeeded.length} of ${result.total}.`
                + (result.failed.length > 0 ? ` ${result.failed.length} failed.` : '')
                + (realignFailures.length > 0 ? ` ${realignFailures.length} sync map${realignFailures.length !== 1 ? 's' : ''} not rebuilt.` : '')
            setBatchResult({
                type: result.failed.length > 0 || realignFailures.length > 0 ? 'error' : 'success',
                text: msg,
                detail: result,
            })
            await onChanged()
        } catch (err) { setBatchResult({ type: 'error', text: err.message }) }
        finally { setBatchBusy(false) }
    }

    const unconverted = files.filter(f => !f.already_converted)

    return (
        <div>
            <p className="system-card-desc">
                MOBI and AZW3 files cannot be read directly. Convert them to EPUB using Calibre or the built-in Python converter.
            </p>

            {canAdmin && files.length > 0 && (
                <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap', alignItems: 'center' }}>
                    {unconverted.length > 0 && (
                        <>
                            <button className="btn btn-sm btn-primary" onClick={() => handleBatchConvert(false)} disabled={batchBusy}>
                                {batchBusy ? 'Converting…' : 'Convert All'}
                            </button>
                            <button className="btn btn-sm btn-danger" onClick={() => handleBatchConvert(true)} disabled={batchBusy}>
                                {batchBusy ? 'Converting…' : 'Convert All & Delete Original'}
                            </button>
                        </>
                    )}
                    <button className="btn btn-sm btn-danger" onClick={() => setForceDeleteConfirm('all')} disabled={batchBusy}>
                        Force Delete All
                    </button>
                </div>
            )}

            {batchResult && (
                <div className={`alert alert-${batchResult.type}`} style={{ marginBottom: 12 }}>
                    {batchResult.text}
                    {batchResult.detail?.failed?.length > 0 && (
                        <ul style={{ marginTop: 8, paddingLeft: 20, fontSize: '0.8rem' }}>
                            {batchResult.detail.failed.map((f, i) => <li key={i}>{f.filename}: {f.error}</li>)}
                        </ul>
                    )}
                    {batchResult.detail?.realign_failures?.length > 0 && (
                        <ul style={{ marginTop: 8, paddingLeft: 20, fontSize: '0.8rem' }}>
                            {batchResult.detail.realign_failures.map((f, i) => (
                                <li key={i}>Sync map for pair {f.pair_id} not rebuilt: {f.error}</li>
                            ))}
                        </ul>
                    )}
                </div>
            )}

            {/* Convert/Delete live in the last column, off the right edge of a
                phone; the card clips, so this scrolls instead of hiding them
                (issue #271). */}
            <div className="table-wrapper table-wrapper--flush">
                <table className="data-table">
                    <thead>
                        <tr>
                            <th>File</th><th>Format</th><th>Size</th><th>Status</th>
                            {canAdmin && <th>Actions</th>}
                        </tr>
                    </thead>
                    <tbody>
                        {files.map(file => (
                            <tr key={file.id}>
                                <td>
                                    <div style={{ fontWeight: 500 }}>{file.title || file.filename}</div>
                                    {file.author && <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>{file.author}</div>}
                                    {fileMessages[file.id] && (
                                        <div className={`system-file-msg ${fileMessages[file.id].type}`}>{fileMessages[file.id].text}</div>
                                    )}
                                </td>
                                <td><span className="system-file-format-badge">{file.format}</span></td>
                                <td style={{ color: 'var(--text-secondary)' }}>{formatBytes(file.file_size)}</td>
                                <td>
                                    {file.already_converted
                                        ? <span className="system-file-converted">Converted</span>
                                        : <span className="system-file-pending">Not converted</span>}
                                </td>
                                {canAdmin && (
                                    <td>
                                        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                                            {!file.already_converted && (
                                                <>
                                                    <button className="btn btn-sm btn-primary" onClick={() => handleConvert(file, false)} disabled={busyIds.has(file.id)}>
                                                        {busyIds.has(file.id) ? '…' : 'Convert'}
                                                    </button>
                                                    <button className="btn btn-sm btn-secondary" onClick={() => handleConvert(file, true)} disabled={busyIds.has(file.id)}>
                                                        {busyIds.has(file.id) ? '…' : 'Convert & Delete'}
                                                    </button>
                                                </>
                                            )}
                                            {file.already_converted && (
                                                <>
                                                    {file.epub_ebook_id && (
                                                        <button className="btn btn-sm btn-secondary" onClick={() => setPreviewFile(file)} disabled={busyIds.has(file.id)}>Preview</button>
                                                    )}
                                                    <button className="btn btn-sm btn-danger" onClick={() => handleDeleteSource(file)} disabled={busyIds.has(file.id)}>
                                                        {busyIds.has(file.id) ? '…' : 'Delete Original'}
                                                    </button>
                                                </>
                                            )}
                                            <button className="btn btn-sm btn-danger" onClick={() => setForceDeleteConfirm({ file })} disabled={busyIds.has(file.id)}>
                                                {busyIds.has(file.id) ? '…' : 'Force Delete'}
                                            </button>
                                        </div>
                                    </td>
                                )}
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>

            {previewFile?.epub_ebook_id && (
                <EbookReader ebookId={previewFile.epub_ebook_id} bookTitle={previewFile.title || previewFile.filename} onClose={() => setPreviewFile(null)} />
            )}

            {forceDeleteConfirm && (
                <Modal onClose={() => setForceDeleteConfirm(null)} labelledBy="force-delete-title">
                    <h3 id="force-delete-title" style={{ marginTop: 0 }}>Confirm Force Delete</h3>
                    <p>
                        {forceDeleteConfirm === 'all'
                            ? 'All unsupported files will be permanently deleted from the filesystem and the library.'
                            : <>The file <strong>{forceDeleteConfirm.file.filename}</strong> will be permanently deleted from the filesystem and the library.</>}
                        {' '}This cannot be undone.
                    </p>
                    <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                        <button className="btn btn-secondary" onClick={() => setForceDeleteConfirm(null)}>Cancel</button>
                        <button className="btn btn-danger" onClick={() => forceDeleteConfirm === 'all' ? handleForceDeleteAll() : handleForceDelete(forceDeleteConfirm.file)}>
                            Delete
                        </button>
                    </div>
                </Modal>
            )}
        </div>
    )
}
