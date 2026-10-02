import { useCallback, useEffect, useRef, useState } from 'react'
import { getSyncMapRebuildStatus, startSyncMapRebuild, cancelSyncMapRebuild } from '../api'
import Modal from './Modal'

/**
 * System page card: rebuild sync maps built by an older sentence splitter
 * (issue #774). Admin-only; the page gates it, the server enforces it.
 *
 * The run itself is a server-side job. This card only starts it, polls its
 * status every two seconds while it is running, and shows the per-pair results
 * of the current or last run.
 */

const POLL_MS = 2000

const OUTCOME_LABELS = {
    rebuilt: 'Rebuilt',
    dry_run: 'Would rebuild',
    failed: 'Failed',
    skipped: 'Skipped',
}

/**
 * Parse the "Only these pair ids" field. Empty means "no filter" (valid, null
 * ids); anything that is not a comma-separated list of positive integers is
 * invalid rather than silently trimmed down, because a typo that quietly
 * dropped an id would rebuild a different set than the admin asked for.
 */
function parsePairIds(text) {
    const trimmed = text.trim()
    if (!trimmed) return { ids: null, valid: true }
    const parts = trimmed.split(',').map((p) => p.trim())
    if (!parts.every((p) => /^\d+$/.test(p) && Number(p) > 0)) return { ids: null, valid: false }
    return { ids: [...new Set(parts.map(Number))], valid: true }
}

const dash = (value) => (value ?? '-')

function pointsCell(row) {
    if (row.old_points == null && row.new_points == null) return '-'
    return `${dash(row.old_points)} → ${dash(row.new_points)}`
}

function movedCell(row) {
    if (row.bookmarks == null) return '-'
    return `${dash(row.bookmarks_remapped)}/${row.bookmarks}`
}

function summaryLine(status) {
    const { dry_run: dryRun, succeeded, failed, skipped, cancelled } = status
    const lead = dryRun ? 'Dry run: ' : ''
    const verb = dryRun ? 'would rebuild' : 'rebuilt'
    return `${lead}${succeeded} ${verb}, ${failed} failed, ${skipped} skipped${cancelled ? ', cancelled' : ''}`
}

export default function SyncMapRebuildCard() {
    const [status, setStatus] = useState(null)
    const [error, setError] = useState(null)
    const [notice, setNotice] = useState(null)
    const [hidden, setHidden] = useState(false)
    const [busy, setBusy] = useState(false)
    const [pairText, setPairText] = useState('')
    const [confirmOpen, setConfirmOpen] = useState(false)
    const mounted = useRef(true)

    useEffect(() => {
        mounted.current = true
        return () => { mounted.current = false }
    }, [])

    const refresh = useCallback(async () => {
        try {
            const next = await getSyncMapRebuildStatus()
            if (!mounted.current) return
            setStatus(next)
            setError(null)
        } catch (err) {
            if (!mounted.current) return
            // 404: a server that predates the endpoint. There is nothing to
            // show, so the card is simply absent.
            if (err.status === 404) setHidden(true)
            else setError(err.message)
        }
    }, [])

    useEffect(() => { refresh() }, [refresh])

    const running = Boolean(status?.running)
    useEffect(() => {
        if (!running) return undefined
        const timer = setInterval(refresh, POLL_MS)
        return () => clearInterval(timer)
    }, [running, refresh])

    const { ids, valid } = parsePairIds(pairText)
    const selected = pairText.trim() !== ''

    const start = async (dryRun) => {
        setConfirmOpen(false)
        setBusy(true)
        setError(null)
        setNotice(null)
        try {
            const next = await startSyncMapRebuild({ dryRun, pairIds: ids })
            if (mounted.current) setStatus(next)
        } catch (err) {
            if (!mounted.current) return
            if (err.status === 409) {
                setNotice('A rebuild is already running.')
                await refresh()
            } else {
                setError(err.message)
            }
        } finally {
            if (mounted.current) setBusy(false)
        }
    }

    const cancel = async () => {
        setBusy(true)
        setError(null)
        try {
            const next = await cancelSyncMapRebuild()
            if (mounted.current) setStatus(next)
        } catch (err) {
            if (mounted.current) setError(err.message)
        } finally {
            if (mounted.current) setBusy(false)
        }
    }

    if (hidden || (!status && !error)) return null

    const results = status?.results ?? []
    const outdated = status?.outdated ?? 0
    const showButtons = !running && (outdated > 0 || pairText.trim() !== '')
    const rebuildLabel = selected
        ? 'Rebuild selected'
        : `Rebuild ${outdated} sync ${outdated === 1 ? 'map' : 'maps'}`

    return (
        <div className="system-card">
            <div className="system-card-header">
                <h3>Rebuild Sync Maps</h3>
            </div>
            <div className="system-card-body">
                {error && <div className="alert alert-error" role="alert">{error}</div>}
                {notice && <div className="alert alert-info" role="status">{notice}</div>}

                {status && running && (
                    <div>
                        <p className="system-card-desc" style={{ marginBottom: 8 }}>
                            {status.dry_run ? 'Dry run in progress (nothing is saved)' : 'Rebuilding sync maps'}
                        </p>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14 }}>
                            <progress
                                value={status.processed}
                                max={Math.max(status.to_process, 1)}
                                style={{ flex: 1, maxWidth: 360 }}
                            />
                            <span>{status.processed} of {status.to_process}</span>
                            <button className="btn btn-secondary" onClick={cancel} disabled={busy}>Cancel</button>
                        </div>
                    </div>
                )}

                {status && !running && (
                    <>
                        {outdated > 0 ? (
                            <>
                                <p style={{ marginTop: 0 }}>
                                    {outdated} of {status.total} sync maps were built by an older version of the sentence splitter.
                                </p>
                                <p className="system-card-desc">
                                    Rebuilding re-aligns each book from its saved transcript. It takes roughly ten seconds
                                    per book, needs no re-transcription, and carries saved positions over. A dry run does
                                    the same work without saving anything.
                                </p>
                            </>
                        ) : (
                            <p className="system-card-desc">All sync maps are up to date.</p>
                        )}

                        {showButtons && (
                            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 14 }}>
                                <button
                                    className="btn btn-secondary"
                                    disabled={busy || !valid}
                                    onClick={() => start(true)}
                                >
                                    Dry run
                                </button>
                                <button
                                    className="btn btn-primary"
                                    disabled={busy || !valid}
                                    onClick={() => setConfirmOpen(true)}
                                >
                                    {rebuildLabel}
                                </button>
                            </div>
                        )}

                        <details style={{ marginBottom: 14 }}>
                            <summary style={{ cursor: 'pointer' }}>Advanced</summary>
                            <div className="system-form-row" style={{ marginTop: 10 }}>
                                <label htmlFor="sync-rebuild-pair-ids" className="system-form-label">Only these pair ids</label>
                                <input
                                    id="sync-rebuild-pair-ids"
                                    type="text"
                                    className="input"
                                    placeholder="e.g. 12, 40"
                                    value={pairText}
                                    onChange={(e) => setPairText(e.target.value)}
                                    style={{ maxWidth: 360 }}
                                />
                                {!valid && (
                                    <p className="system-form-hint error" style={{ marginTop: 6 }}>
                                        Enter comma-separated pair ids, such as 12, 40.
                                    </p>
                                )}
                            </div>
                        </details>

                        {(status.finished_at || results.length > 0) && <p style={{ fontWeight: 600 }}>{summaryLine(status)}</p>}
                    </>
                )}

                {results.length > 0 && (
                    <div className="table-wrapper table-wrapper--flush">
                        <table className="data-table">
                            <thead>
                                <tr>
                                    <th>Pair</th>
                                    <th>Outcome</th>
                                    <th>Points (old → new)</th>
                                    <th>Matched</th>
                                    <th>Multi-line before</th>
                                    <th>Positions moved</th>
                                    <th>Detail</th>
                                </tr>
                            </thead>
                            <tbody>
                                {results.map((row) => (
                                    <tr key={row.pair_id}>
                                        <td>{row.pair_id}</td>
                                        <td>{OUTCOME_LABELS[row.outcome] ?? row.outcome}</td>
                                        <td>{pointsCell(row)}</td>
                                        <td>{dash(row.matched)}</td>
                                        <td>{dash(row.old_multiline_points)}</td>
                                        <td>{movedCell(row)}</td>
                                        <td>{row.detail || '-'}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>

            {confirmOpen && (
                <Modal onClose={() => setConfirmOpen(false)} labelledBy="sync-rebuild-confirm-title">
                    <h3 id="sync-rebuild-confirm-title" style={{ marginTop: 0 }}>
                        {selected ? 'Rebuild the selected sync maps?' : `Rebuild ${outdated} sync ${outdated === 1 ? 'map' : 'maps'}?`}
                    </h3>
                    <p>
                        Each book is re-aligned from its saved transcript and the old sync map is replaced.
                        Saved positions are carried over. This takes roughly ten seconds per book.
                    </p>
                    <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                        <button className="btn btn-secondary" onClick={() => setConfirmOpen(false)}>Cancel</button>
                        <button className="btn btn-primary" onClick={() => start(false)}>Rebuild</button>
                    </div>
                </Modal>
            )}
        </div>
    )
}
