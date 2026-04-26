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
import './SystemPage.css'

function StatusPill({ status }) {
    if (!status) return null
    const cls = {
        succeeded: 'status-pill-ok',
        running: 'status-pill-info',
        failed: 'status-pill-err',
    }[status] || 'status-pill-info'
    return <span className={`status-pill ${cls}`}>{status}</span>
}

function formatDate(iso) {
    if (!iso) return 'never'
    const d = new Date(iso)
    return d.toLocaleString()
}

function SourceCard({ source, onChange, onTriggerSync, children }) {
    return (
        <div className="system-card">
            <div className="system-card-header">
                <h3>{source.display_name}</h3>
                <StatusPill status={source.last_status} />
            </div>
            <div className="system-card-body">
                <div className="import-source-meta">
                    <div>
                        <strong>Connected:</strong> {source.connected ? 'Yes' : 'No'}
                    </div>
                    <div>
                        <strong>Last sync:</strong> {formatDate(source.last_sync_at)}
                    </div>
                    {source.last_message && (
                        <div className="import-source-msg">{source.last_message}</div>
                    )}
                </div>

                {source.supports_auto_sync && (
                    <div className="import-source-controls">
                        <label>
                            <input
                                type="checkbox"
                                disabled={!source.connected}
                                checked={source.auto_sync_enabled}
                                onChange={(e) =>
                                    onChange({ auto_sync_enabled: e.target.checked })
                                }
                            />{' '}
                            Auto-sync
                        </label>
                        <label>
                            Every{' '}
                            <input
                                type="number"
                                min="1"
                                max="168"
                                value={source.cadence_hours}
                                onChange={(e) =>
                                    onChange({
                                        cadence_hours: parseInt(e.target.value || '24', 10),
                                    })
                                }
                                style={{ width: 60 }}
                            />{' '}
                            hours
                        </label>
                        <button
                            className="btn"
                            disabled={!source.connected}
                            onClick={() => onTriggerSync()}
                        >
                            Run sync now
                        </button>
                    </div>
                )}

                {children}
            </div>
        </div>
    )
}

function AudibleConnectDialog({ onClose, onConnected }) {
    const [step, setStep] = useState('idle') // idle | started | submitting | done
    const [loginUrl, setLoginUrl] = useState('')
    const [stateToken, setStateToken] = useState('')
    const [responseUrl, setResponseUrl] = useState('')
    const [error, setError] = useState(null)

    const start = async () => {
        setError(null)
        try {
            const r = await audibleLoginStart()
            setLoginUrl(r.login_url)
            setStateToken(r.state_token)
            setStep('started')
        } catch (e) {
            setError(e.message)
        }
    }

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
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-content" onClick={(e) => e.stopPropagation()}>
                <h3>Connect Audible</h3>
                {step === 'idle' && (
                    <>
                        <p>
                            We'll print an Amazon login URL. Open it in this browser, sign in
                            (including any 2FA), then copy the URL of the resulting blank page
                            and paste it back here.
                        </p>
                        <button className="btn primary" onClick={start}>
                            Start
                        </button>
                    </>
                )}
                {(step === 'started' || step === 'submitting') && (
                    <>
                        <p>1. Open this URL and complete sign-in:</p>
                        <p>
                            <a href={loginUrl} target="_blank" rel="noreferrer">
                                {loginUrl}
                            </a>
                        </p>
                        <p>
                            2. After login Amazon redirects you to a page that may look blank
                            ("https://www.amazon.com/ap/maplanding?..."). Copy that full URL
                            from your browser's address bar and paste here:
                        </p>
                        <textarea
                            rows="3"
                            value={responseUrl}
                            onChange={(e) => setResponseUrl(e.target.value)}
                            style={{ width: '100%' }}
                        />
                        <button
                            className="btn primary"
                            disabled={!responseUrl || step === 'submitting'}
                            onClick={submit}
                        >
                            {step === 'submitting' ? 'Connecting…' : 'Submit'}
                        </button>
                    </>
                )}
                {step === 'done' && <p>Connected.</p>}
                {error && <p className="error">{error}</p>}
                <button className="btn" onClick={onClose}>
                    Close
                </button>
            </div>
        </div>
    )
}

function AcsmUploadPanel({ onUploaded }) {
    const [busy, setBusy] = useState(false)
    const [results, setResults] = useState([])
    const inputRef = useRef(null)

    const handleFiles = async (files) => {
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
        setResults((prev) => [...out, ...prev])
        setBusy(false)
        onUploaded()
    }

    return (
        <div className="acsm-upload-panel">
            <p>
                Drop one or more <code>.acsm</code> (Google Play / Nook DRM stub) or
                <code>.epub</code> files. ACSM files are converted to DRM-free EPUB on
                the server.
            </p>
            <div
                className="acsm-dropzone"
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => {
                    e.preventDefault()
                    handleFiles(Array.from(e.dataTransfer.files))
                }}
                onClick={() => inputRef.current?.click()}
            >
                {busy ? 'Uploading…' : 'Drop files here or click to browse'}
                <input
                    ref={inputRef}
                    type="file"
                    multiple
                    accept=".acsm,.epub"
                    style={{ display: 'none' }}
                    onChange={(e) => handleFiles(Array.from(e.target.files || []))}
                />
            </div>
            {results.length > 0 && (
                <ul className="acsm-results">
                    {results.map((r, i) => (
                        <li key={i}>
                            <strong>{r.name}</strong>: {r.status}
                            {r.title && ` — ${r.title}`}
                            {r.error && <span className="error"> ({r.error})</span>}
                        </li>
                    ))}
                </ul>
            )}
        </div>
    )
}

export default function ImportSourcesPage() {
    const [sources, setSources] = useState([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)
    const [showAudibleDialog, setShowAudibleDialog] = useState(false)

    const refresh = useCallback(async () => {
        try {
            const data = await listImportSources()
            setSources(data)
        } catch (e) {
            setError(e.message)
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => {
        refresh()
        const handle = setInterval(refresh, 5000)
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
            // The job will appear in the next refresh tick.
        } catch (e) {
            setError(e.message)
        }
    }

    const disconnectAudible = async () => {
        await audibleDisconnect()
        refresh()
    }

    if (loading) return <div className="page">Loading…</div>

    const audible = sources.find((s) => s.source_key === 'audible')
    const acsm = sources.find((s) => s.source_key === 'acsm')

    return (
        <div className="page system-page">
            <h2>Import Sources</h2>
            {error && <div className="error">{error}</div>}

            {audible && (
                <SourceCard
                    source={audible}
                    onChange={(patch) => updateConfig('audible', patch)}
                    onTriggerSync={() => runSync('audible')}
                >
                    {audible.connected ? (
                        <button className="btn" onClick={disconnectAudible}>
                            Disconnect
                        </button>
                    ) : (
                        <button
                            className="btn primary"
                            onClick={() => setShowAudibleDialog(true)}
                        >
                            Connect Audible
                        </button>
                    )}
                </SourceCard>
            )}

            {acsm && (
                <SourceCard
                    source={acsm}
                    onChange={(patch) => updateConfig('acsm', patch)}
                    onTriggerSync={() => runSync('acsm')}
                >
                    <AcsmUploadPanel onUploaded={refresh} />
                </SourceCard>
            )}

            <div className="system-card">
                <div className="system-card-header">
                    <h3>Kindle</h3>
                </div>
                <div className="system-card-body">
                    <p>
                        Kindle imports are not currently supported on this server because
                        Amazon's DRM stripping requires Kindle for PC (Windows or macOS) plus
                        the Calibre DeDRM plugin. See the documentation for the recommended
                        manual workflow, or consider purchasing future ebooks from Google
                        Play / Nook (use ACSM upload above).
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
