import React, { useState, useEffect } from 'react'
import {
    getDiskUsage, getSettings, updateSettings, testRemoteConnection,
    testAbsConnection, enrichLibraryFromAbs, getUnsupportedFiles,
    convertUnsupportedFile, convertAllUnsupportedFiles, deleteUnsupportedSource,
    getCalibreStatus
} from '../api'
import { useAuth } from '../contexts/AuthContext'
import EbookReader from '../components/EbookReader'
import './SystemPage.css'

/* ── StatCard ──────────────────────────────────────────────────────── */
function StatCard({ title, icon, color, usedHuman, totalHuman, usedBytes, totalBytes }) {
    const percent = totalBytes > 0 ? Math.min(100, (usedBytes / totalBytes) * 100) : 0

    const icons = {
        book: (
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="24" height="24">
                <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
                <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
            </svg>
        ),
        headphones: (
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="24" height="24">
                <circle cx="12" cy="12" r="10" />
                <polygon points="10 8 16 12 10 16 10 8" />
            </svg>
        ),
        database: (
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="24" height="24">
                <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" />
                <polyline points="3.27 6.96 12 12.01 20.73 6.96" />
                <line x1="12" y1="22.08" x2="12" y2="12" />
            </svg>
        )
    }

    return (
        <div className="system-stat-card">
            <div className="system-stat-card-top">
                <div className={`system-stat-icon ${color}`}>
                    {icons[icon]}
                </div>
                <div>
                    <div className="system-stat-title">{title}</div>
                    <div className="system-stat-value">{usedHuman}</div>
                    <div className="system-stat-sub">of {totalHuman}</div>
                </div>
            </div>
            <div className="progress-bar" style={{ height: '6px', background: 'var(--bg-input)' }}>
                <div
                    className="progress-fill"
                    style={{
                        width: `${percent}%`,
                        background: percent > 90 ? 'var(--error)' : percent > 75 ? 'var(--warning)' : 'var(--accent)'
                    }}
                />
            </div>
            <div className="system-stat-pct">{percent.toFixed(1)}% used</div>
        </div>
    )
}

/* ── TableRow ──────────────────────────────────────────────────────── */
function TableRow({ label, used, total, free, percent }) {
    return (
        <tr>
            <td>{label}</td>
            <td style={{ fontWeight: 600 }}>{used}</td>
            <td style={{ color: 'var(--text-secondary)' }}>{total}</td>
            <td style={{ color: 'var(--success)' }}>{free} available</td>
            <td>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <div className="progress-bar" style={{ width: '60px', height: '4px' }}>
                        <div
                            className="progress-fill"
                            style={{
                                width: `${Math.min(100, percent)}%`,
                                background: percent > 90 ? 'var(--error)' : percent > 75 ? 'var(--warning)' : 'var(--accent)'
                            }}
                        />
                    </div>
                    <span style={{ fontSize: '0.8rem' }}>{percent.toFixed(1)}%</span>
                </div>
            </td>
        </tr>
    )
}

/* ── CalibreStatusSection ──────────────────────────────────────────── */
function CalibreStatusSection() {
    const [status, setStatus] = useState(null)
    const [checking, setChecking] = useState(false)

    useEffect(() => { checkStatus() }, [])

    const checkStatus = async () => {
        setChecking(true)
        try {
            const data = await getCalibreStatus()
            setStatus(data)
        } catch (err) {
            setStatus({ available: false, error: err.message })
        } finally {
            setChecking(false)
        }
    }

    return (
        <div className="system-card">
            <div className="system-card-header">
                <h3>Calibre (MOBI/AZW3 Conversion)</h3>
                <button className="btn btn-secondary" onClick={checkStatus} disabled={checking}>
                    {checking ? 'Checking...' : 'Check'}
                </button>
            </div>
            <div className="system-card-body">
                <p className="system-card-desc">
                    Calibre's <code>ebook-convert</code> is used to convert MOBI and AZW3 files to EPUB.
                    It must be installed in the server container.
                </p>
                {status === null ? (
                    <span style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>Checking…</span>
                ) : status.available ? (
                    <div className="system-calibre-status">
                        <span className="system-calibre-available">Available</span>
                        <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>{status.version}</span>
                    </div>
                ) : (
                    <div>
                        <span className="system-calibre-unavailable">Not Available</span>
                        <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '4px', marginBottom: 0 }}>
                            {status.error}
                        </p>
                    </div>
                )}
            </div>
        </div>
    )
}

/* ── SettingsSection ───────────────────────────────────────────────── */
function SettingsSection() {
    const { hasMinRole } = useAuth()
    const canAdmin = hasMinRole('admin')
    const [ebookPatterns, setEbookPatterns] = useState('')
    const [audiobookPatterns, setAudiobookPatterns] = useState('')
    const [loading, setLoading] = useState(false)
    const [msg, setMsg] = useState(null)

    useEffect(() => { loadSettings() }, [])

    const loadSettings = async () => {
        try {
            const settings = await getSettings()
            if (settings.ebook_filename_patterns) setEbookPatterns(settings.ebook_filename_patterns.join('\n'))
            if (settings.audiobook_filename_patterns) setAudiobookPatterns(settings.audiobook_filename_patterns.join('\n'))
        } catch (err) {
            console.error(err)
        }
    }

    const handleSave = async () => {
        setLoading(true)
        setMsg(null)
        try {
            await updateSettings({
                ebook_filename_patterns: ebookPatterns.split('\n').filter(p => p.trim() !== ''),
                audiobook_filename_patterns: audiobookPatterns.split('\n').filter(p => p.trim() !== ''),
            })
            setMsg({ type: 'success', text: 'Settings saved successfully' })
        } catch (err) {
            setMsg({ type: 'error', text: 'Failed to save settings' })
        } finally {
            setLoading(false)
        }
    }

    return (
        <div className="system-card">
            <div className="system-card-header">
                <h3>Library Settings</h3>
            </div>
            <div className="system-card-body">
                <p className="system-card-desc">
                    Enter one pattern per line. Tokens: <code>&lt;Author&gt;</code>, <code>&lt;Series&gt;</code>, <code>&lt;Book Number&gt;</code>, <code>&lt;Title&gt;</code>.
                    Use <code>/</code> to match directory structure (e.g. <code>&lt;Author&gt;/&lt;Series&gt;/&lt;Title&gt;</code>).
                </p>

                <div className="system-form-row">
                    <label className="system-form-label">EBook Filename Patterns</label>
                    <textarea
                        className="input"
                        rows={5}
                        value={ebookPatterns}
                        onChange={e => setEbookPatterns(e.target.value)}
                        placeholder="<Author> - [<Series> <Book Number>] - <Title>"
                        style={{ fontFamily: 'monospace', width: '100%' }}
                    />
                </div>

                <div className="system-form-row">
                    <label className="system-form-label">Audiobook Filename Patterns</label>
                    <textarea
                        className="input"
                        rows={5}
                        value={audiobookPatterns}
                        onChange={e => setAudiobookPatterns(e.target.value)}
                        placeholder="<Author> - [<Series> <Book Number>] - <Title>"
                        style={{ fontFamily: 'monospace', width: '100%' }}
                    />
                </div>

                {msg && (
                    <div className={`alert alert-${msg.type}`} style={{ marginBottom: '16px' }}>
                        {msg.text}
                    </div>
                )}

                {canAdmin ? (
                    <button className="btn btn-primary" onClick={handleSave} disabled={loading}>
                        {loading ? 'Saving...' : 'Save Settings'}
                    </button>
                ) : (
                    <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>
                        Admin role required to change settings.
                    </p>
                )}
            </div>
        </div>
    )
}

/* ── TranscriptionSettingsSection ─────────────────────────────────── */
function TranscriptionSettingsSection() {
    const [provider, setProvider] = useState('remote_with_fallback')
    const [remoteUrl, setRemoteUrl] = useState('')
    const [remoteTimeout, setRemoteTimeout] = useState(7200)
    const [autoTranscribe, setAutoTranscribe] = useState(false)
    const [whisperModel, setWhisperModel] = useState('medium')
    const [loading, setLoading] = useState(false)
    const [msg, setMsg] = useState(null)
    const [testResult, setTestResult] = useState(null)
    const [isTesting, setIsTesting] = useState(false)

    useEffect(() => { loadSettings() }, [])

    const loadSettings = async () => {
        try {
            const settings = await getSettings()
            if (settings.transcription_provider) setProvider(settings.transcription_provider)
            if (settings.transcription_remote_url !== undefined) setRemoteUrl(settings.transcription_remote_url)
            if (settings.transcription_remote_timeout !== undefined) setRemoteTimeout(settings.transcription_remote_timeout)
            if (settings.auto_transcribe_enabled !== undefined) setAutoTranscribe(settings.auto_transcribe_enabled)
            if (settings.whisper_model) setWhisperModel(settings.whisper_model)
        } catch (err) {
            console.error(err)
        }
    }

    const handleSave = async () => {
        setLoading(true)
        setMsg(null)
        try {
            await updateSettings({
                transcription_provider: provider,
                transcription_remote_url: remoteUrl,
                transcription_remote_timeout: parseInt(remoteTimeout) || 7200,
                auto_transcribe_enabled: autoTranscribe,
                whisper_model: whisperModel,
            })
            setMsg({ type: 'success', text: 'Transcription settings saved' })
        } catch (err) {
            setMsg({ type: 'error', text: 'Failed to save transcription settings' })
        } finally {
            setLoading(false)
        }
    }

    const handleTestConnection = async () => {
        if (!remoteUrl) {
            setTestResult({ success: false, text: "You didn't enter a URL yet!" })
            return
        }
        setIsTesting(true)
        setTestResult(null)
        try {
            const result = await testRemoteConnection(remoteUrl)
            if (result.success === true) {
                setTestResult({
                    success: true,
                    text: `Connection successful! GPU: ${result.gpu_name || 'None'}. Model: ${result.model_loaded ? 'Loaded' : 'Not Loaded'}`
                })
            } else {
                setTestResult({ success: false, text: 'Server responded, but status is not healthy.' })
            }
        } catch (err) {
            setTestResult({ success: false, text: `Connection failed: ${err.message}` })
        } finally {
            setIsTesting(false)
        }
    }

    return (
        <div className="system-card">
            <div className="system-card-header">
                <h3>Transcription Settings</h3>
            </div>
            <div className="system-card-body">
                <p className="system-card-desc">
                    Configure how books are transcribed and synchronized. Remote transcription via Jetson Orin Nano is highly recommended for speed.
                </p>

                <div className="system-form-row">
                    <label className="system-form-label">Transcription Backend</label>
                    <select
                        className="input"
                        value={provider}
                        onChange={e => setProvider(e.target.value)}
                        style={{ width: '100%', maxWidth: '400px' }}
                    >
                        <option value="remote_with_fallback">Remote with Fallback (Recommended)</option>
                        <option value="remote">Remote Only</option>
                        <option value="local">Local Only</option>
                    </select>
                </div>

                {provider !== 'local' && (
                    <div className="system-form-row">
                        <label className="system-form-label">Jetson Remote URL</label>
                        <div className="system-form-inline">
                            <input
                                type="text"
                                className="input"
                                value={remoteUrl}
                                onChange={e => setRemoteUrl(e.target.value)}
                                placeholder="http://192.168.1.100:9000"
                                style={{ flex: '1 1 300px' }}
                            />
                            <button
                                className="btn btn-secondary"
                                onClick={handleTestConnection}
                                disabled={isTesting}
                                style={{ whiteSpace: 'nowrap' }}
                            >
                                {isTesting ? 'Testing...' : 'Test Connection'}
                            </button>
                        </div>
                        {testResult && (
                            <div className={`system-test-result ${testResult.success ? 'success' : 'error'}`}>
                                {testResult.success ? '✅ ' : '❌ '}{testResult.text}
                            </div>
                        )}

                        <div className="system-form-row" style={{ marginTop: '16px' }}>
                            <label className="system-form-label">Remote Timeout (Seconds)</label>
                            <input
                                type="number"
                                className="input"
                                value={remoteTimeout}
                                onChange={e => setRemoteTimeout(e.target.value)}
                                style={{ width: '150px' }}
                            />
                            <p className="system-form-hint">
                                How long to wait for a 10+ hour audiobook before failing (default: 7200s / 2 hours).
                            </p>
                        </div>
                    </div>
                )}

                <div className="system-form-row">
                    <label className="system-form-label">Local Whisper Model</label>
                    <select
                        className="input"
                        value={whisperModel}
                        onChange={e => setWhisperModel(e.target.value)}
                        style={{ width: '100%', maxWidth: '200px' }}
                    >
                        <option value="tiny">Tiny (Fastest, least accurate)</option>
                        <option value="base">Base</option>
                        <option value="small">Small</option>
                        <option value="medium">Medium (Recommended)</option>
                        <option value="large-v3">Large v3 (Slowest, most accurate)</option>
                    </select>
                    <p className="system-form-hint">
                        Takes effect only when using the Local Whisper backend.
                    </p>
                </div>

                <div className="system-form-toggle">
                    <input
                        type="checkbox"
                        id="autoTranscribeToggle"
                        checked={autoTranscribe}
                        onChange={e => setAutoTranscribe(e.target.checked)}
                    />
                    <div>
                        <label htmlFor="autoTranscribeToggle" className="system-form-toggle-label">
                            Auto-transcribe new books
                        </label>
                        <p className="system-form-toggle-hint">
                            Automatically add newly matched book pairs to the transcription queue during library scans.
                        </p>
                    </div>
                </div>

                {msg && (
                    <div className={`alert alert-${msg.type}`} style={{ marginBottom: '16px' }}>
                        {msg.text}
                    </div>
                )}

                <button className="btn btn-primary" onClick={handleSave} disabled={loading}>
                    {loading ? 'Saving...' : 'Save Transcription Settings'}
                </button>
            </div>
        </div>
    )
}

/* ── ABSSettingsSection ────────────────────────────────────────────── */
function ABSSettingsSection() {
    const [enabled, setEnabled] = useState(false)
    const [url, setUrl] = useState('')
    const [token, setToken] = useState('')
    const [prefix, setPrefix] = useState('')
    const [loading, setLoading] = useState(false)
    const [msg, setMsg] = useState(null)
    const [testResult, setTestResult] = useState(null)
    const [isTesting, setIsTesting] = useState(false)
    const [isEnriching, setIsEnriching] = useState(false)

    useEffect(() => { loadSettings() }, [])

    const loadSettings = async () => {
        try {
            const s = await getSettings()
            if (s.abs_enabled !== undefined) setEnabled(s.abs_enabled === true || s.abs_enabled === 'true')
            if (s.abs_url !== undefined) setUrl(s.abs_url || '')
            if (s.abs_api_token !== undefined) setToken(s.abs_api_token || '')
            if (s.abs_audiobooks_prefix !== undefined) setPrefix(s.abs_audiobooks_prefix || '')
        } catch (err) {
            console.error(err)
        }
    }

    const handleSave = async () => {
        setLoading(true)
        setMsg(null)
        try {
            await updateSettings({ abs_enabled: enabled, abs_url: url, abs_api_token: token, abs_audiobooks_prefix: prefix })
            setMsg({ type: 'success', text: 'Audiobookshelf settings saved' })
        } catch (err) {
            setMsg({ type: 'error', text: 'Failed to save settings' })
        } finally {
            setLoading(false)
        }
    }

    const handleTest = async () => {
        if (!url || !token) {
            setTestResult({ success: false, text: 'Enter a URL and API token first' })
            return
        }
        setIsTesting(true)
        setTestResult(null)
        try {
            const result = await testAbsConnection(url, token)
            const libs = result.book_libraries?.join(', ') || 'none found'
            setTestResult({ success: true, text: `Connected! Book libraries: ${libs}` })
        } catch (err) {
            setTestResult({ success: false, text: err.message })
        } finally {
            setIsTesting(false)
        }
    }

    const handleEnrichAll = async () => {
        setIsEnriching(true)
        setMsg(null)
        try {
            const result = await enrichLibraryFromAbs()
            setMsg({ type: 'success', text: result.message })
        } catch (err) {
            setMsg({ type: 'error', text: `Enrichment failed: ${err.message}` })
        } finally {
            setIsEnriching(false)
        }
    }

    return (
        <div className="system-card">
            <div className="system-card-header">
                <h3>Audiobookshelf Integration</h3>
            </div>
            <div className="system-card-body">
                <p className="system-card-desc">
                    Enrich audiobook metadata (descriptions, narrators, series, genres, etc.) from your Audiobookshelf library.
                    Enriched data is written back into audio file tags so future scans don't need to re-query ABS.
                </p>

                <div className="system-form-toggle">
                    <input
                        type="checkbox"
                        id="absEnabledToggle"
                        checked={enabled}
                        onChange={e => setEnabled(e.target.checked)}
                    />
                    <div>
                        <label htmlFor="absEnabledToggle" className="system-form-toggle-label">
                            Enrich metadata with Audiobookshelf
                        </label>
                        <p className="system-form-toggle-hint">
                            Automatically enrich audiobooks during library scans.
                        </p>
                    </div>
                </div>

                {enabled && (
                    <div className="system-form-indent">
                        <div className="system-form-row">
                            <label className="system-form-label">Audiobookshelf URL</label>
                            <input
                                type="text"
                                className="input"
                                value={url}
                                onChange={e => setUrl(e.target.value)}
                                placeholder="http://audiobookshelf:80"
                                style={{ width: '100%', maxWidth: '400px' }}
                            />
                            <p className="system-form-hint">
                                Internal Docker hostname (e.g. <code>http://audiobookshelf:80</code>) or LAN IP.
                            </p>
                        </div>

                        <div className="system-form-row">
                            <label className="system-form-label">API Token</label>
                            <div className="system-form-inline">
                                <input
                                    type="password"
                                    className="input"
                                    value={token}
                                    onChange={e => setToken(e.target.value)}
                                    placeholder="Paste your ABS API token"
                                    style={{ flex: '1 1 300px' }}
                                />
                                <button
                                    className="btn btn-secondary"
                                    onClick={handleTest}
                                    disabled={isTesting}
                                    style={{ whiteSpace: 'nowrap' }}
                                >
                                    {isTesting ? 'Testing...' : 'Test Connection'}
                                </button>
                            </div>
                            <p className="system-form-hint">
                                Found in ABS → Settings → Users → your user → API Token.
                            </p>
                            {testResult && (
                                <div className={`system-test-result ${testResult.success ? 'success' : 'error'}`}>
                                    {testResult.success ? '✅ ' : '❌ '}{testResult.text}
                                </div>
                            )}
                        </div>

                        <div className="system-form-row">
                            <label className="system-form-label">Audiobooks Path Prefix (ABS internal)</label>
                            <input
                                type="text"
                                className="input"
                                value={prefix}
                                onChange={e => setPrefix(e.target.value)}
                                placeholder="/audiobooks"
                                style={{ width: '100%', maxWidth: '400px' }}
                            />
                            <p className="system-form-hint">
                                The path prefix ABS uses inside its container (e.g. <code>/audiobooks</code>).
                                Check ABS → Libraries → your library → folder path.
                            </p>
                        </div>
                    </div>
                )}

                {msg && (
                    <div className={`alert alert-${msg.type}`} style={{ marginBottom: '16px' }}>
                        {msg.text}
                    </div>
                )}

                <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                    <button className="btn btn-primary" onClick={handleSave} disabled={loading}>
                        {loading ? 'Saving...' : 'Save Settings'}
                    </button>
                    {enabled && (
                        <button
                            className="btn btn-secondary"
                            onClick={handleEnrichAll}
                            disabled={isEnriching}
                            title="Force re-enrich all audiobooks from ABS, overwriting existing metadata"
                        >
                            {isEnriching ? 'Enriching...' : 'Re-enrich All from ABS'}
                        </button>
                    )}
                </div>
            </div>
        </div>
    )
}

/* ── UnsupportedFilesTab ───────────────────────────────────────────── */
function UnsupportedFilesTab({ canAdmin }) {
    const [files, setFiles] = useState([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)
    const [busyIds, setBusyIds] = useState(new Set())
    const [batchBusy, setBatchBusy] = useState(false)
    const [batchResult, setBatchResult] = useState(null)
    const [fileMessages, setFileMessages] = useState({})
    const [previewFile, setPreviewFile] = useState(null)

    useEffect(() => { loadFiles() }, [])

    const loadFiles = async () => {
        setLoading(true)
        setError(null)
        try {
            const data = await getUnsupportedFiles()
            setFiles(data)
        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }

    const setFileBusy = (id, busy) => {
        setBusyIds(prev => {
            const next = new Set(prev)
            busy ? next.add(id) : next.delete(id)
            return next
        })
    }

    const setFileMsg = (id, msg) => setFileMessages(prev => ({ ...prev, [id]: msg }))

    const handleConvert = async (file, deleteSource) => {
        setFileBusy(file.id, true)
        setFileMsg(file.id, null)
        try {
            await convertUnsupportedFile(file.id, deleteSource)
            setFileMsg(file.id, { type: 'success', text: deleteSource ? 'Converted & source deleted' : 'Converted to EPUB' })
            await loadFiles()
        } catch (err) {
            setFileMsg(file.id, { type: 'error', text: err.message })
        } finally {
            setFileBusy(file.id, false)
        }
    }

    const handleDeleteSource = async (file) => {
        setFileBusy(file.id, true)
        setFileMsg(file.id, null)
        try {
            await deleteUnsupportedSource(file.id)
            await loadFiles()
        } catch (err) {
            setFileMsg(file.id, { type: 'error', text: err.message })
        } finally {
            setFileBusy(file.id, false)
        }
    }

    const handleBatchConvert = async (deleteSource) => {
        setBatchBusy(true)
        setBatchResult(null)
        try {
            const result = await convertAllUnsupportedFiles(deleteSource)
            const msg = `Converted ${result.succeeded.length} of ${result.total} files.` +
                (result.failed.length > 0 ? ` ${result.failed.length} failed.` : '')
            setBatchResult({ type: result.failed.length > 0 ? 'error' : 'success', text: msg, detail: result })
            await loadFiles()
        } catch (err) {
            setBatchResult({ type: 'error', text: err.message })
        } finally {
            setBatchBusy(false)
        }
    }

    const formatBytes = (bytes) => {
        if (!bytes) return '—'
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
        return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
    }

    const unconverted = files.filter(f => !f.already_converted)

    return (
        <div>
            {/* Sub-toolbar */}
            <div style={{ display: 'flex', gap: '8px', marginBottom: '16px', flexWrap: 'wrap', alignItems: 'center' }}>
                <button className="btn btn-secondary" onClick={loadFiles} disabled={loading || batchBusy}>
                    Refresh
                </button>
                {canAdmin && unconverted.length > 0 && (
                    <>
                        <button className="btn btn-primary" onClick={() => handleBatchConvert(false)} disabled={batchBusy}>
                            {batchBusy ? 'Converting...' : 'Convert All'}
                        </button>
                        <button className="btn btn-danger" onClick={() => handleBatchConvert(true)} disabled={batchBusy}>
                            {batchBusy ? 'Converting...' : 'Convert All & Delete Original'}
                        </button>
                    </>
                )}
            </div>

            {batchResult && (
                <div className={`alert alert-${batchResult.type}`} style={{ marginBottom: '16px' }}>
                    {batchResult.text}
                    {batchResult.detail?.failed?.length > 0 && (
                        <ul style={{ marginTop: '8px', paddingLeft: '20px', fontSize: '0.8rem' }}>
                            {batchResult.detail.failed.map((f, i) => (
                                <li key={i}>{f.filename}: {f.error}</li>
                            ))}
                        </ul>
                    )}
                </div>
            )}

            <div className="system-card">
                <div className="system-card-header">
                    <h3>MOBI / AZW3 Files</h3>
                </div>
                <div className="system-card-body" style={{ paddingTop: '12px', paddingBottom: '12px' }}>
                    <p className="system-card-desc" style={{ marginBottom: 0 }}>
                        MOBI and AZW3 files cannot be read directly. Convert them to EPUB using Calibre (if installed) or the built-in Python converter.
                    </p>
                </div>

                {loading ? (
                    <div style={{ padding: '32px', textAlign: 'center' }}><div className="spinner"></div></div>
                ) : error ? (
                    <div className="alert alert-error" style={{ margin: '16px' }}>{error}</div>
                ) : files.length === 0 ? (
                    <div style={{ padding: '32px', textAlign: 'center', color: 'var(--text-muted)' }}>
                        No unsupported files found in your library.
                    </div>
                ) : (
                    <table className="data-table">
                        <thead>
                            <tr>
                                <th>File</th>
                                <th>Format</th>
                                <th>Size</th>
                                <th>Status</th>
                                {canAdmin && <th>Actions</th>}
                            </tr>
                        </thead>
                        <tbody>
                            {files.map(file => (
                                <tr key={file.id}>
                                    <td>
                                        <div style={{ fontWeight: 500 }}>{file.title || file.filename}</div>
                                        {file.author && (
                                            <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>{file.author}</div>
                                        )}
                                        {fileMessages[file.id] && (
                                            <div className={`system-file-msg ${fileMessages[file.id].type}`}>
                                                {fileMessages[file.id].text}
                                            </div>
                                        )}
                                    </td>
                                    <td>
                                        <span className="system-file-format-badge">{file.format}</span>
                                    </td>
                                    <td style={{ color: 'var(--text-secondary)' }}>{formatBytes(file.file_size)}</td>
                                    <td>
                                        {file.already_converted ? (
                                            <span className="system-file-converted">Converted</span>
                                        ) : (
                                            <span className="system-file-pending">Not converted</span>
                                        )}
                                    </td>
                                    {canAdmin && (
                                        <td>
                                            <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                                                {!file.already_converted && (
                                                    <>
                                                        <button
                                                            className="btn btn-sm btn-primary"
                                                            onClick={() => handleConvert(file, false)}
                                                            disabled={busyIds.has(file.id)}
                                                        >
                                                            {busyIds.has(file.id) ? '...' : 'Convert'}
                                                        </button>
                                                        <button
                                                            className="btn btn-sm btn-secondary"
                                                            onClick={() => handleConvert(file, true)}
                                                            disabled={busyIds.has(file.id)}
                                                        >
                                                            {busyIds.has(file.id) ? '...' : 'Convert & Delete'}
                                                        </button>
                                                    </>
                                                )}
                                                {file.already_converted && (
                                                    <>
                                                        {file.epub_ebook_id && (
                                                            <button
                                                                className="btn btn-sm btn-secondary"
                                                                onClick={() => setPreviewFile(file)}
                                                                disabled={busyIds.has(file.id)}
                                                            >
                                                                Preview
                                                            </button>
                                                        )}
                                                        <button
                                                            className="btn btn-sm btn-danger"
                                                            onClick={() => handleDeleteSource(file)}
                                                            disabled={busyIds.has(file.id)}
                                                        >
                                                            {busyIds.has(file.id) ? '...' : 'Delete Original'}
                                                        </button>
                                                    </>
                                                )}
                                            </div>
                                        </td>
                                    )}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </div>

            {previewFile?.epub_ebook_id && (
                <EbookReader
                    ebookId={previewFile.epub_ebook_id}
                    bookTitle={previewFile.title || previewFile.filename}
                    onClose={() => setPreviewFile(null)}
                />
            )}
        </div>
    )
}

/* ── SystemPage ────────────────────────────────────────────────────── */
function SystemPage({ tab }) {
    const { hasMinRole } = useAuth()
    const canAdmin = hasMinRole('admin')
    const [activeTab, setActiveTab] = useState(tab || 'status')
    const [stats, setStats] = useState(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)

    // Sync activeTab when the route prop changes (deep links still work)
    useEffect(() => {
        if (tab) setActiveTab(tab)
    }, [tab])

    useEffect(() => {
        if (activeTab === 'status') loadStats()
    }, [activeTab])

    const loadStats = async () => {
        setLoading(true)
        try {
            const data = await getDiskUsage()
            setStats(data)
            setError(null)
        } catch (err) {
            setError('Failed to load system statistics')
        } finally {
            setLoading(false)
        }
    }

    return (
        <div>
            {/* ── Toolbar ── */}
            <div className="system-toolbar">
                <div className="system-toolbar-left">
                    <div className="library-filter-pills">
                        <button
                            className={`library-filter-pill${activeTab === 'status' ? ' active' : ''}`}
                            onClick={() => setActiveTab('status')}
                        >
                            Status
                        </button>
                        <button
                            className={`library-filter-pill${activeTab === 'unsupported' ? ' active' : ''}`}
                            onClick={() => setActiveTab('unsupported')}
                        >
                            Unsupported Files
                        </button>
                    </div>
                </div>
            </div>

            {/* ── Status tab ── */}
            {activeTab === 'status' && (
                loading ? (
                    <div className="loading-page"><div className="spinner"></div></div>
                ) : error ? (
                    <div className="alert alert-error">
                        {error}
                        <button className="btn btn-sm btn-secondary" onClick={loadStats} style={{ marginLeft: 'auto' }}>Retry</button>
                    </div>
                ) : (
                    <>
                        <div className="system-stat-grid">
                            <StatCard
                                title="EBook Library"
                                icon="book"
                                color="purple"
                                usedHuman={stats.ebook_used_human}
                                totalHuman={stats.ebook_total_human}
                                usedBytes={stats.ebook_used_bytes}
                                totalBytes={stats.ebook_total_bytes}
                            />
                            <StatCard
                                title="Audiobook Library"
                                icon="headphones"
                                color="green"
                                usedHuman={stats.audiobook_used_human}
                                totalHuman={stats.audiobook_total_human}
                                usedBytes={stats.audiobook_used_bytes}
                                totalBytes={stats.audiobook_total_bytes}
                            />
                            <StatCard
                                title="App Data & DB"
                                icon="database"
                                color="blue"
                                usedHuman={stats.app_data_used_human}
                                totalHuman={stats.app_data_total_human}
                                usedBytes={stats.app_data_used_bytes}
                                totalBytes={stats.app_data_total_bytes}
                            />
                        </div>

                        <CalibreStatusSection />
                        <SettingsSection />
                        <TranscriptionSettingsSection />
                        <ABSSettingsSection />

                        <div className="system-card">
                            <div className="system-card-header">
                                <h3>Detailed Breakdown</h3>
                            </div>
                            <table className="data-table">
                                <thead>
                                    <tr>
                                        <th>Category</th>
                                        <th>Used</th>
                                        <th>Total Capacity</th>
                                        <th>Free</th>
                                        <th>% Used</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    <TableRow
                                        label="Ebooks"
                                        used={stats.ebook_used_human}
                                        total={stats.ebook_total_human}
                                        free={stats.ebook_free_human}
                                        percent={stats.ebook_total_bytes > 0 ? (stats.ebook_used_bytes / stats.ebook_total_bytes * 100) : 0}
                                    />
                                    <TableRow
                                        label="Audiobooks"
                                        used={stats.audiobook_used_human}
                                        total={stats.audiobook_total_human}
                                        free={stats.audiobook_free_human}
                                        percent={stats.audiobook_total_bytes > 0 ? (stats.audiobook_used_bytes / stats.audiobook_total_bytes * 100) : 0}
                                    />
                                    <TableRow
                                        label="Data & Database"
                                        used={stats.app_data_used_human}
                                        total={stats.app_data_total_human}
                                        free={stats.app_data_free_human}
                                        percent={stats.app_data_total_bytes > 0 ? (stats.app_data_used_bytes / stats.app_data_total_bytes * 100) : 0}
                                    />
                                </tbody>
                            </table>
                        </div>
                    </>
                )
            )}

            {/* ── Unsupported Files tab ── */}
            {activeTab === 'unsupported' && <UnsupportedFilesTab canAdmin={canAdmin} />}
        </div>
    )
}

export default SystemPage
