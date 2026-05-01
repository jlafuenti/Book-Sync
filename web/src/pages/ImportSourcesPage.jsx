import React, { useCallback, useEffect, useRef, useState } from 'react'
import {
    listImportSources,
    updateImportSourceConfig,
    triggerImportSync,
    getImportJobs,
    audibleLoginStart,
    audibleLoginComplete,
    audibleDisconnect,
    uploadAcsm,
} from '../api'
import './ImportSourcesPage.css'

/* ── Helpers ─────────────────────────────────────────────────────── */

function formatRelativeTime(iso) {
    if (!iso) return 'Never synced'
    const then = new Date(iso)
    const diffSec = Math.floor((Date.now() - then.getTime()) / 1000)
    if (diffSec < 30) return 'Just now'
    if (diffSec < 60) return `${diffSec}s ago`
    if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`
    if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`
    if (diffSec < 86400 * 7) return `${Math.floor(diffSec / 86400)}d ago`
    return then.toLocaleDateString()
}

function pillFor(source) {
    const { last_status: status, connected, progress } = source
    // Treat 'running' as actually running only if the backend is also
    // sending live progress data — guards against stale rows from a crash.
    const reallyRunning =
        status === 'running' &&
        progress &&
        (progress.current != null || progress.total != null || progress.title)
    if (reallyRunning) return { cls: 'running', label: 'Syncing' }
    if (status === 'failed') return { cls: 'err', label: 'Last sync failed' }
    if (status === 'succeeded') return { cls: 'ok', label: 'Healthy' }
    if (!connected) return { cls: 'idle', label: 'Not connected' }
    return { cls: 'idle', label: 'Idle' }
}

/* ── Status pill ─────────────────────────────────────────────────── */

function StatusPill({ source }) {
    const { cls, label } = pillFor(source)
    return (
        <span className={`import-status-pill ${cls}`}>
            <span className="dot" />
            {label}
        </span>
    )
}

/* ── Source-shared chrome ────────────────────────────────────────── */

function SourceCardShell({ icon, source, children }) {
    return (
        <div className="system-card">
            <div className="system-card-header import-source-header">
                <h3>
                    <span className="import-source-icon">{icon}</span>
                    {source.display_name}
                </h3>
                <StatusPill source={source} />
            </div>
            <div className="system-card-body">{children}</div>
        </div>
    )
}

function StateGrid({ source }) {
    return (
        <div className="import-state-grid">
            <div className="import-state-cell">
                <span className="import-state-cell-label">Status</span>
                <span className={`import-state-cell-value${source.connected ? '' : ' muted'}`}>
                    {source.connected ? 'Connected' : 'Not connected'}
                </span>
            </div>
            <div className="import-state-cell">
                <span className="import-state-cell-label">Last sync</span>
                <span className={`import-state-cell-value${source.last_sync_at ? '' : ' muted'}`}>
                    {formatRelativeTime(source.last_sync_at)}
                </span>
            </div>
            {source.supports_auto_sync && (
                <div className="import-state-cell">
                    <span className="import-state-cell-label">Auto-sync</span>
                    <span className={`import-state-cell-value${source.auto_sync_enabled ? '' : ' muted'}`}>
                        {source.auto_sync_enabled ? `Every ${source.cadence_hours}h` : 'Off'}
                    </span>
                </div>
            )}
        </div>
    )
}

/* ── Live progress bar ───────────────────────────────────────────── */

function ProgressBlock({ source }) {
    // Only render when the source genuinely is mid-sync. last_status alone
    // isn't trusted — we also require some sign of actual progress data
    // (a current count, total, or title) to guard against stale rows.
    if (source.last_status !== 'running') return null
    const progress = source.progress
    if (!progress) return null
    const hasSignal = progress.current != null || progress.total != null || progress.title
    if (!hasSignal) return null

    const { current, total, title } = progress
    const pct = total ? Math.min(100, Math.round((current / total) * 100)) : 0
    const indeterminate = !total
    return (
        <div className="import-progress">
            <div className="import-progress-row">
                <span className="import-progress-title" title={title}>
                    {title || 'Starting…'}
                </span>
                {!indeterminate && total > 0 && (
                    <span className="import-progress-count">
                        {current} / {total}
                    </span>
                )}
            </div>
            <div className="import-progress-bar">
                <div
                    className={`import-progress-bar-fill${indeterminate ? ' indeterminate' : ''}`}
                    style={!indeterminate ? { width: `${pct}%` } : {}}
                />
            </div>
        </div>
    )
}

/* ── Last-run summary + collapsible details ──────────────────────── */

function LastRunSummary({ source, jobs }) {
    const [showErrors, setShowErrors] = useState(false)
    const [showAdded, setShowAdded] = useState(false)

    if (source.last_status === 'running') return null
    if (!source.last_sync_at) return null

    const latest = jobs && jobs[0]
    const errors = (latest && latest.errors) || []
    const added = (latest && latest.added_titles) || []
    const skipped = (latest && latest.items_skipped) || 0
    const fatal = latest && latest.error_message

    return (
        <div className="import-summary">
            {fatal ? (
                <div>
                    <strong style={{ color: 'var(--error, #ef4444)' }}>
                        Sync could not start:
                    </strong>{' '}
                    {fatal}
                </div>
            ) : (
                <>
                    <div className="import-summary-counts">
                        {added.length > 0 && (
                            <span className="import-summary-count added">
                                <strong>{added.length}</strong> added
                            </span>
                        )}
                        {skipped > 0 && (
                            <span className="import-summary-count">
                                <strong>{skipped}</strong> already in library
                            </span>
                        )}
                        {errors.length > 0 && (
                            <span className="import-summary-count error">
                                <strong>{errors.length}</strong> failed
                            </span>
                        )}
                        {added.length === 0 && skipped === 0 && errors.length === 0 && (
                            <span className="import-summary-count">
                                Nothing new to import
                            </span>
                        )}
                    </div>
                    {added.length > 0 && (
                        <button
                            className="import-summary-toggle"
                            onClick={() => setShowAdded((v) => !v)}
                        >
                            {showAdded ? 'Hide' : 'Show'} added books
                        </button>
                    )}
                    {showAdded && (
                        <ul className="import-added-list">
                            {added.map((t, i) => (
                                <li key={i}>{t}</li>
                            ))}
                        </ul>
                    )}
                    {errors.length > 0 && (
                        <button
                            className="import-summary-toggle"
                            onClick={() => setShowErrors((v) => !v)}
                            style={{ marginLeft: added.length > 0 ? 16 : 0 }}
                        >
                            {showErrors ? 'Hide' : 'Show'} errors
                        </button>
                    )}
                    {showErrors && (
                        <ul className="import-error-list">
                            {errors.map((e, i) => (
                                <li key={i} className="import-error-item">
                                    <div className="import-error-item-title">
                                        {e.title || e.external_id || 'Unknown'}
                                    </div>
                                    <div className="import-error-item-msg">{e.error}</div>
                                </li>
                            ))}
                        </ul>
                    )}
                </>
            )}
        </div>
    )
}

/* ── Auto-sync controls + actions row ────────────────────────────── */

function ControlsRow({ source, onChange, onTriggerSync, secondaryAction }) {
    const isRunning = source.last_status === 'running'
    return (
        <>
            {source.supports_auto_sync && source.connected && (
                <div className="import-controls">
                    <label className="import-controls-toggle">
                        <input
                            type="checkbox"
                            checked={source.auto_sync_enabled}
                            onChange={(e) =>
                                onChange({ auto_sync_enabled: e.target.checked })
                            }
                        />
                        Auto-sync
                    </label>
                    <div className="import-controls-cadence">
                        every
                        <input
                            type="number"
                            min="1"
                            max="168"
                            disabled={!source.auto_sync_enabled}
                            value={source.cadence_hours}
                            onChange={(e) =>
                                onChange({
                                    cadence_hours: parseInt(e.target.value || '24', 10),
                                })
                            }
                        />
                        hours
                    </div>
                </div>
            )}
            <div className="import-action-row">
                {source.connected && (
                    <button
                        className="btn btn-primary"
                        disabled={isRunning}
                        onClick={onTriggerSync}
                    >
                        {isRunning ? 'Syncing…' : 'Run sync now'}
                    </button>
                )}
                {secondaryAction}
            </div>
        </>
    )
}

/* ── Audible card ────────────────────────────────────────────────── */

function AudibleCard({ source, jobs, onChange, onTriggerSync, onConnect, onDisconnect }) {
    return (
        <SourceCardShell
            icon={<AudibleIcon />}
            source={source}
        >
            <StateGrid source={source} />
            <ProgressBlock source={source} />
            <LastRunSummary source={source} jobs={jobs} />
            <ControlsRow
                source={source}
                onChange={onChange}
                onTriggerSync={onTriggerSync}
                secondaryAction={
                    source.connected ? (
                        <button className="btn btn-secondary" onClick={onDisconnect}>
                            Disconnect
                        </button>
                    ) : (
                        <button className="btn btn-primary" onClick={onConnect}>
                            Connect Audible
                        </button>
                    )
                }
            />
        </SourceCardShell>
    )
}

/* ── ACSM card ───────────────────────────────────────────────────── */

function AcsmCard({ source, jobs, onChange, onTriggerSync, onUploaded }) {
    const [busy, setBusy] = useState(false)
    const [results, setResults] = useState([])
    const [dragging, setDragging] = useState(false)
    const inputRef = useRef(null)

    const handleFiles = async (files) => {
        if (!files || files.length === 0) return
        setBusy(true)
        const out = []
        for (const file of files) {
            try {
                const r = await uploadAcsm(file)
                out.push({ name: file.name, ...r })
            } catch (e) {
                out.push({ name: file.name, status: 'failed', error: e.message })
            }
        }
        setResults((prev) => [...out, ...prev].slice(0, 20))
        setBusy(false)
        onUploaded()
    }

    return (
        <SourceCardShell
            icon={<EpubIcon />}
            source={source}
        >
            <StateGrid source={source} />
            <ProgressBlock source={source} />
            <LastRunSummary source={source} jobs={jobs} />

            <div
                className={`acsm-dropzone${dragging ? ' dragging' : ''}${busy ? ' busy' : ''}`}
                onDragOver={(e) => {
                    e.preventDefault()
                    setDragging(true)
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={(e) => {
                    e.preventDefault()
                    setDragging(false)
                    handleFiles(Array.from(e.dataTransfer.files))
                }}
                onClick={() => !busy && inputRef.current?.click()}
            >
                <UploadIcon />
                <div className="acsm-dropzone-primary">
                    {busy
                        ? 'Uploading…'
                        : dragging
                            ? 'Drop to upload'
                            : 'Drag .acsm or .epub files here'}
                </div>
                <div className="acsm-dropzone-hint">
                    Or click to browse. Google Play / Nook downloads are
                    converted to DRM-free EPUB on the server.
                </div>
                <input
                    ref={inputRef}
                    type="file"
                    multiple
                    accept=".acsm,.epub"
                    style={{ display: 'none' }}
                    onChange={(e) => handleFiles(Array.from(e.target.files || []))}
                />
            </div>

            <p className="acsm-watched-folder">
                Or drop files server-side into <code>/data/imports/acsm/inbox/</code>{' '}
                — they're picked up automatically.
            </p>

            {results.length > 0 && (
                <ul className="acsm-results">
                    {results.map((r, i) => {
                        const cls = r.status === 'added'
                            ? 'added'
                            : r.status === 'skipped'
                                ? 'skipped'
                                : 'failed'
                        const icon = r.status === 'added' ? '✓' : r.status === 'skipped' ? '–' : '✕'
                        return (
                            <li key={i} className={`acsm-result-item ${cls}`}>
                                <span className={`acsm-result-icon ${cls}`}>{icon}</span>
                                <span className="acsm-result-name" title={r.name}>
                                    {r.title || r.name}
                                </span>
                                <span className="acsm-result-detail">
                                    {r.status === 'added' && 'Added to library'}
                                    {r.status === 'skipped' && (r.reason || 'Already in library')}
                                    {r.status === 'failed' && (r.error || 'Failed')}
                                </span>
                            </li>
                        )
                    })}
                </ul>
            )}

            <ControlsRow
                source={source}
                onChange={onChange}
                onTriggerSync={onTriggerSync}
            />
        </SourceCardShell>
    )
}

/* ── Audible login modal ─────────────────────────────────────────── */

function AudibleConnectDialog({ onClose, onConnected }) {
    const [step, setStep] = useState('idle') // idle | started | submitting | done
    const [loginUrl, setLoginUrl] = useState('')
    const [stateToken, setStateToken] = useState('')
    const [responseUrl, setResponseUrl] = useState('')
    const [error, setError] = useState(null)

    useEffect(() => {
        // Auto-start the flow when the modal opens.
        if (step !== 'idle') return
        const start = async () => {
            try {
                const r = await audibleLoginStart()
                setLoginUrl(r.login_url)
                setStateToken(r.state_token)
                setStep('started')
            } catch (e) {
                setError(e.message)
            }
        }
        start()
    }, [step])

    const submit = async () => {
        setError(null)
        setStep('submitting')
        try {
            await audibleLoginComplete(stateToken, responseUrl.trim())
            setStep('done')
            onConnected()
        } catch (e) {
            setError(e.message)
            setStep('started')
        }
    }

    return (
        <div className="import-modal-overlay" onClick={onClose}>
            <div className="import-modal" onClick={(e) => e.stopPropagation()}>
                <h3>Connect Audible</h3>
                {step === 'idle' && <p className="import-modal-step">Preparing login URL…</p>}
                {(step === 'started' || step === 'submitting') && (
                    <>
                        <p className="import-modal-step">
                            <strong>Step 1.</strong> Open this URL in your browser and sign in to
                            Amazon (including any 2FA prompt). After login Amazon will redirect
                            you to a page that may look blank — that's expected.
                        </p>
                        <a
                            href={loginUrl}
                            target="_blank"
                            rel="noreferrer"
                            className="import-modal-url"
                        >
                            {loginUrl}
                        </a>

                        <p className="import-modal-step">
                            <strong>Step 2.</strong> Copy the <em>full</em> URL of that
                            blank-looking page from your browser's address bar (it will start
                            with <code>https://www.amazon.com/ap/maplanding?...</code>) and
                            paste it here:
                        </p>
                        <textarea
                            className="import-modal-textarea"
                            value={responseUrl}
                            onChange={(e) => setResponseUrl(e.target.value)}
                            placeholder="https://www.amazon.com/ap/maplanding?..."
                        />

                        {error && <div className="import-modal-error">{error}</div>}

                        <div className="import-modal-actions">
                            <button className="btn btn-secondary" onClick={onClose}>
                                Cancel
                            </button>
                            <button
                                className="btn btn-primary"
                                disabled={!responseUrl || step === 'submitting'}
                                onClick={submit}
                            >
                                {step === 'submitting' ? 'Connecting…' : 'Connect'}
                            </button>
                        </div>
                    </>
                )}
                {step === 'done' && (
                    <>
                        <p className="import-modal-step">Connected.</p>
                        <div className="import-modal-actions">
                            <button className="btn btn-primary" onClick={onClose}>Close</button>
                        </div>
                    </>
                )}
            </div>
        </div>
    )
}

/* ── Icons ───────────────────────────────────────────────────────── */

function AudibleIcon() {
    return (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 18v-6a9 9 0 0 1 18 0v6" />
            <path d="M21 19a2 2 0 0 1-2 2h-1v-7h3v5zM3 19a2 2 0 0 0 2 2h1v-7H3v5z" />
        </svg>
    )
}

function EpubIcon() {
    return (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
            <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
        </svg>
    )
}

function UploadIcon() {
    return (
        <svg className="acsm-dropzone-icon" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
            <polyline points="17 8 12 3 7 8" />
            <line x1="12" y1="3" x2="12" y2="15" />
        </svg>
    )
}

function KindleIcon() {
    return (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <rect x="4" y="2" width="16" height="20" rx="2" />
            <line x1="8" y1="18" x2="16" y2="18" />
        </svg>
    )
}

/* ── Page ────────────────────────────────────────────────────────── */

export default function ImportSourcesPage() {
    const [sources, setSources] = useState([])
    const [jobsBySource, setJobsBySource] = useState({})
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)
    const [showAudibleDialog, setShowAudibleDialog] = useState(false)

    const refresh = useCallback(async () => {
        try {
            const data = await listImportSources()
            setSources(data)
            const next = {}
            await Promise.all(
                data.map(async (s) => {
                    try {
                        next[s.source_key] = await getImportJobs(s.source_key)
                    } catch { /* ignore — surface in next refresh if persistent */ }
                })
            )
            setJobsBySource(next)
        } catch (e) {
            setError(e.message)
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => {
        refresh()
        // Poll faster while a sync is running, slower otherwise.
        const handle = setInterval(refresh, 3000)
        return () => clearInterval(handle)
    }, [refresh])

    const updateConfig = async (sourceKey, patch) => {
        try {
            await updateImportSourceConfig(sourceKey, patch)
            await refresh()
        } catch (e) {
            setError(e.message)
        }
    }

    const runSync = async (sourceKey) => {
        try {
            await triggerImportSync(sourceKey)
        } catch (e) {
            setError(e.message)
        }
    }

    const disconnectAudible = async () => {
        await audibleDisconnect()
        refresh()
    }

    if (loading) {
        return (
            <div className="import-page">
                <div className="import-page-header">
                    <h2>Import Sources</h2>
                </div>
                <p style={{ color: 'var(--text-muted)' }}>Loading…</p>
            </div>
        )
    }

    const audible = sources.find((s) => s.source_key === 'audible')
    const acsm = sources.find((s) => s.source_key === 'acsm')

    return (
        <div className="import-page">
            <div className="import-page-header">
                <h2>Import Sources</h2>
                <p>
                    Pull purchased books and audiobooks into Tandem from the services you
                    already use.
                </p>
            </div>
            {error && <div className="import-page-error">{error}</div>}

            {audible && (
                <AudibleCard
                    source={audible}
                    jobs={jobsBySource.audible}
                    onChange={(patch) => updateConfig('audible', patch)}
                    onTriggerSync={() => runSync('audible')}
                    onConnect={() => setShowAudibleDialog(true)}
                    onDisconnect={disconnectAudible}
                />
            )}

            {acsm && (
                <AcsmCard
                    source={acsm}
                    jobs={jobsBySource.acsm}
                    onChange={(patch) => updateConfig('acsm', patch)}
                    onTriggerSync={() => runSync('acsm')}
                    onUploaded={refresh}
                />
            )}

            <div className="system-card kindle-card">
                <div className="system-card-header">
                    <h3>
                        <span className="import-source-icon"><KindleIcon /></span>
                        Kindle
                    </h3>
                    <span className="import-status-pill idle">
                        <span className="dot" />
                        Not supported
                    </span>
                </div>
                <div className="system-card-body">
                    <p>
                        Kindle imports require Amazon's DRM to be removed via Kindle for PC
                        (Windows or macOS) plus Calibre's DeDRM plugin — that's a desktop-only
                        toolchain that can't run inside this Linux server.
                    </p>
                    <p>
                        If you want hands-off ebook syncing, switching new purchases to
                        Google Play or Nook is the easiest path: their downloads come as
                        <code> .acsm</code> files that this server can convert to DRM-free
                        EPUB automatically (drop them in the card above).
                    </p>
                </div>
            </div>

            {showAudibleDialog && (
                <AudibleConnectDialog
                    onClose={() => setShowAudibleDialog(false)}
                    onConnected={() => {
                        setShowAudibleDialog(false)
                        refresh()
                    }}
                />
            )}
        </div>
    )
}
