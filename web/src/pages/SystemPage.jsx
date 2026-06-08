import React, { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import {
    getDiskUsage, getSettings, updateSettings, testRemoteConnection,
    testAbsConnection, enrichLibraryFromAbs, getUnsupportedFiles,
    convertUnsupportedFile, convertAllUnsupportedFiles, deleteUnsupportedSource,
    forceDeleteUnsupportedFile, forceDeleteAllUnsupportedFiles,
    getCalibreStatus, getEbooks, getAudiobooks, getPairs, getTranscriptionQueue
} from '../api'
import { useAuth } from '../contexts/AuthContext'
import { UserManagementSection } from './UserManagementPage'
import EbookReader from '../components/EbookReader'
import useIsMobile from '../hooks/useIsMobile'
import './SystemPage.css'

/* ── Helpers ───────────────────────────────────────────────────────── */
function formatBytes(bytes) {
    if (!bytes || bytes === 0) return '0 B'
    if (bytes >= 1e12) return `${(bytes / 1e12).toFixed(1)} TB`
    if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`
    if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(1)} MB`
    return `${Math.round(bytes / 1024)} KB`
}

/* ── CollapsibleCard ───────────────────────────────────────────────── */
// Supports both controlled (open + onToggle props) and uncontrolled (defaultOpen) modes.
function CollapsibleCard({ title, open: controlledOpen, onToggle, defaultOpen = false, children }) {
    const [internalOpen, setInternalOpen] = useState(defaultOpen)
    const isControlled = controlledOpen !== undefined
    const open = isControlled ? controlledOpen : internalOpen
    const toggle = isControlled ? onToggle : () => setInternalOpen(o => !o)

    return (
        <div className="system-card">
            <div className="system-card-header system-card-header-clickable" onClick={toggle}>
                <h3>{title}</h3>
                <svg
                    className={`system-chevron${open ? ' open' : ''}`}
                    viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
                    width="16" height="16"
                >
                    <polyline points="6 9 12 15 18 9" />
                </svg>
            </div>
            {open && <div className="system-card-body">{children}</div>}
        </div>
    )
}

/* ── DiskUsageCard ─────────────────────────────────────────────────── */
function DiskUsageCard({ stats }) {
    const usedBytes = (stats.ebook_used_bytes || 0) + (stats.audiobook_used_bytes || 0) + (stats.app_data_used_bytes || 0)
    const totalBytes = (stats.ebook_total_bytes || 0) + (stats.audiobook_total_bytes || 0) + (stats.app_data_total_bytes || 0)
    const freeBytes = totalBytes - usedBytes
    const percent = totalBytes > 0 ? Math.min(100, (usedBytes / totalBytes) * 100) : 0
    const r = 28
    const circ = 2 * Math.PI * r
    const dashOffset = circ * (1 - percent / 100)
    const fillColor = percent > 90 ? 'var(--error)' : percent > 75 ? 'var(--warning)' : 'var(--accent)'

    return (
        <div className="system-stat-card system-stat-card-disk">
            <div>
                <p className="system-stat-badge-label">Disk Usage</p>
                <h4 className="system-stat-badge-value">{formatBytes(usedBytes)}</h4>
                <p className="system-stat-badge-sub">{formatBytes(freeBytes)} free</p>
            </div>
            <div className="system-donut-wrap">
                <svg viewBox="0 0 64 64" className="system-donut" style={{ transform: 'rotate(-90deg)' }}>
                    <circle cx="32" cy="32" r={r} fill="transparent" className="system-donut-track"
                        strokeWidth="6" />
                    <circle cx="32" cy="32" r={r} fill="transparent"
                        strokeWidth="6"
                        strokeDasharray={circ}
                        strokeDashoffset={dashOffset}
                        style={{ stroke: fillColor, transition: 'stroke-dashoffset 0.5s ease' }}
                    />
                </svg>
                <span className="system-donut-pct">{Math.round(percent)}%</span>
            </div>
        </div>
    )
}

/* ── StatBadgeCard ─────────────────────────────────────────────────── */
function StatBadgeCard({ icon, color, label, value, sub, trend }) {
    return (
        <div className="system-stat-card">
            <div className={`system-stat-icon ${color}`}>{icon}</div>
            <p className="system-stat-badge-label">{label}</p>
            <h4 className="system-stat-badge-value">{value}</h4>
            {trend && <p className={`system-stat-badge-trend ${trend.positive ? 'positive' : ''}`}>{trend.text}</p>}
            {sub && !trend && <p className="system-stat-badge-sub">{sub}</p>}
        </div>
    )
}

/* ── CalibreStatusCard ─────────────────────────────────────────────── */
function CalibreStatusCard() {
    const [status, setStatus] = useState(null)
    const [checking, setChecking] = useState(false)

    useEffect(() => { checkStatus() }, [])

    const checkStatus = async () => {
        setChecking(true)
        try { setStatus(await getCalibreStatus()) }
        catch (err) { setStatus({ available: false, error: err.message }) }
        finally { setChecking(false) }
    }

    return (
        <div className="system-status-card">
            <div className="system-status-card-left">
                {status === null || checking ? (
                    <div className="system-status-dot checking" />
                ) : status.available ? (
                    <div className="system-status-dot active" />
                ) : (
                    <div className="system-status-dot error" />
                )}
                <div>
                    <h5 className="system-status-title">Calibre Status</h5>
                    <p className="system-status-desc">
                        {status === null ? 'Checking…' :
                         status.available ? `Available — ${status.version || 'installed'}` :
                         status.error || 'Not available'}
                    </p>
                </div>
            </div>
            <div className="system-status-card-right">
                {status !== null && (
                    <span className={`system-status-badge ${status.available ? 'active' : 'error'}`}>
                        {status.available ? 'ACTIVE' : 'OFFLINE'}
                    </span>
                )}
                <button className="btn btn-secondary" style={{ fontSize: '0.78rem', padding: '3px 10px' }}
                    onClick={checkStatus} disabled={checking}>
                    {checking ? '…' : 'Check'}
                </button>
            </div>
        </div>
    )
}

/* ── UnsupportedFilesCard ──────────────────────────────────────────── */
function UnsupportedFilesCard({ count, onViewItems }) {
    const hasItems = count > 0
    return (
        <div className="system-status-card">
            <div className="system-status-card-left">
                <div className={`system-status-icon-sm ${hasItems ? 'error' : 'ok'}`}>
                    {hasItems ? (
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="18" height="18">
                            <circle cx="12" cy="12" r="10" /><line x1="12" y1="8" x2="12" y2="12" /><line x1="12" y1="16" x2="12.01" y2="16" />
                        </svg>
                    ) : (
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="18" height="18">
                            <polyline points="20 6 9 17 4 12" />
                        </svg>
                    )}
                </div>
                <div>
                    <h5 className="system-status-title">Unsupported Files</h5>
                    <p className="system-status-desc">
                        {count === null ? 'Loading…' :
                         hasItems ? <><span className="system-status-count-error">{count} items</span> require conversion</> :
                         'No unsupported files found'}
                    </p>
                </div>
            </div>
            {hasItems && (
                <button className="system-status-link" onClick={onViewItems}>
                    VIEW ITEMS
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="12" height="12">
                        <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" /><polyline points="15 3 21 3 21 9" /><line x1="10" y1="14" x2="21" y2="3" />
                    </svg>
                </button>
            )}
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
        } catch (err) { console.error(err) }
    }

    const handleSave = async () => {
        setLoading(true)
        setMsg(null)
        try {
            await updateSettings({
                ebook_filename_patterns: ebookPatterns.split('\n').filter(p => p.trim() !== ''),
                audiobook_filename_patterns: audiobookPatterns.split('\n').filter(p => p.trim() !== ''),
            })
            setMsg({ type: 'success', text: 'Settings saved' })
        } catch (err) {
            setMsg({ type: 'error', text: 'Failed to save' })
        } finally { setLoading(false) }
    }

    return (
        <>
            <p className="system-card-desc">
                One pattern per line. Tokens: <code>&lt;Author&gt;</code>, <code>&lt;Series&gt;</code>,{' '}
                <code>&lt;Book Number&gt;</code>, <code>&lt;Title&gt;</code>. Use <code>/</code> for directories.
            </p>
            <div className="system-form-row">
                <label className="system-form-label">EBook Patterns</label>
                <textarea className="input" rows={4} value={ebookPatterns}
                    onChange={e => setEbookPatterns(e.target.value)}
                    placeholder="<Author> - [<Series> <Book Number>] - <Title>"
                    style={{ fontFamily: 'monospace', width: '100%' }} />
            </div>
            <div className="system-form-row">
                <label className="system-form-label">Audiobook Patterns</label>
                <textarea className="input" rows={4} value={audiobookPatterns}
                    onChange={e => setAudiobookPatterns(e.target.value)}
                    placeholder="<Author> - [<Series> <Book Number>] - <Title>"
                    style={{ fontFamily: 'monospace', width: '100%' }} />
            </div>
            {msg && <div className={`alert alert-${msg.type}`} style={{ marginBottom: 12 }}>{msg.text}</div>}
            {canAdmin
                ? <button className="btn btn-primary" onClick={handleSave} disabled={loading}>{loading ? 'Saving…' : 'Save'}</button>
                : <p className="system-form-hint">Admin role required.</p>}
        </>
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
            const s = await getSettings()
            if (s.transcription_provider) setProvider(s.transcription_provider)
            if (s.transcription_remote_url !== undefined) setRemoteUrl(s.transcription_remote_url)
            if (s.transcription_remote_timeout !== undefined) setRemoteTimeout(s.transcription_remote_timeout)
            if (s.auto_transcribe_enabled !== undefined) setAutoTranscribe(s.auto_transcribe_enabled)
            if (s.whisper_model) setWhisperModel(s.whisper_model)
        } catch (err) { console.error(err) }
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
            setMsg({ type: 'success', text: 'Saved' })
        } catch (err) { setMsg({ type: 'error', text: 'Failed to save' }) }
        finally { setLoading(false) }
    }

    const handleTest = async () => {
        if (!remoteUrl) { setTestResult({ success: false, text: "Enter a URL first" }); return }
        setIsTesting(true); setTestResult(null)
        try {
            const r = await testRemoteConnection(remoteUrl)
            setTestResult(r.success === true
                ? { success: true, text: `✅ Connected! GPU: ${r.gpu_name || 'None'}, Model: ${r.model_loaded ? 'Loaded' : 'Not Loaded'}` }
                : { success: false, text: '❌ Server responded but not healthy' })
        } catch (err) { setTestResult({ success: false, text: `❌ ${err.message}` }) }
        finally { setIsTesting(false) }
    }

    return (
        <>
            <div className="system-form-row">
                <label className="system-form-label">Backend</label>
                <select className="input" value={provider} onChange={e => setProvider(e.target.value)} style={{ maxWidth: 360 }}>
                    <option value="remote_with_fallback">Remote with Fallback (Recommended)</option>
                    <option value="remote">Remote Only</option>
                    <option value="local">Local Only</option>
                </select>
            </div>
            {provider !== 'local' && (
                <div className="system-form-row">
                    <label className="system-form-label">Jetson Remote URL</label>
                    <div className="system-form-inline">
                        <input type="text" className="input" value={remoteUrl}
                            onChange={e => setRemoteUrl(e.target.value)}
                            placeholder="http://192.168.1.100:9000"
                            style={{ flex: '1 1 240px' }} />
                        <button className="btn btn-secondary" onClick={handleTest} disabled={isTesting} style={{ whiteSpace: 'nowrap' }}>
                            {isTesting ? 'Testing…' : 'Test Connection'}
                        </button>
                    </div>
                    {testResult && <p className={`system-form-hint ${testResult.success ? 'success' : 'error'}`} style={{ marginTop: 6 }}>{testResult.text}</p>}
                    <div className="system-form-row" style={{ marginTop: 12 }}>
                        <label className="system-form-label">Remote Timeout (s)</label>
                        <input type="number" className="input" value={remoteTimeout}
                            onChange={e => setRemoteTimeout(e.target.value)} style={{ width: 120 }} />
                        <p className="system-form-hint">Default: 7200s (2 hours)</p>
                    </div>
                </div>
            )}
            <div className="system-form-row">
                <label className="system-form-label">Local Whisper Model</label>
                <select className="input" value={whisperModel} onChange={e => setWhisperModel(e.target.value)} style={{ maxWidth: 240 }}>
                    <option value="tiny">Tiny (Fastest)</option>
                    <option value="base">Base</option>
                    <option value="small">Small</option>
                    <option value="medium">Medium (Recommended)</option>
                    <option value="large-v3">Large v3 (Most Accurate)</option>
                </select>
            </div>
            <div className="system-form-toggle">
                <input type="checkbox" id="autoTranscribeToggle" checked={autoTranscribe}
                    onChange={e => setAutoTranscribe(e.target.checked)} />
                <div>
                    <label htmlFor="autoTranscribeToggle" className="system-form-toggle-label">Auto-transcribe new books</label>
                    <p className="system-form-toggle-hint">Auto-queue newly matched pairs during library scans.</p>
                </div>
            </div>
            {msg && <div className={`alert alert-${msg.type}`} style={{ marginBottom: 12 }}>{msg.text}</div>}
            <button className="btn btn-primary" onClick={handleSave} disabled={loading}>{loading ? 'Saving…' : 'Save'}</button>
        </>
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
        } catch (err) { console.error(err) }
    }

    const handleSave = async () => {
        setLoading(true); setMsg(null)
        try {
            await updateSettings({ abs_enabled: enabled, abs_url: url, abs_api_token: token, abs_audiobooks_prefix: prefix })
            setMsg({ type: 'success', text: 'Saved' })
        } catch { setMsg({ type: 'error', text: 'Failed to save' }) }
        finally { setLoading(false) }
    }

    const handleTest = async () => {
        if (!url || !token) { setTestResult({ success: false, text: 'Enter URL and token first' }); return }
        setIsTesting(true); setTestResult(null)
        try {
            const r = await testAbsConnection(url, token)
            setTestResult({ success: true, text: `✅ Connected! Libraries: ${r.book_libraries?.join(', ') || 'none'}` })
        } catch (err) { setTestResult({ success: false, text: `❌ ${err.message}` }) }
        finally { setIsTesting(false) }
    }

    const handleEnrichAll = async () => {
        setIsEnriching(true); setMsg(null)
        try { const r = await enrichLibraryFromAbs(); setMsg({ type: 'success', text: r.message }) }
        catch (err) { setMsg({ type: 'error', text: `Enrichment failed: ${err.message}` }) }
        finally { setIsEnriching(false) }
    }

    return (
        <>
            <p className="system-card-desc">Enrich audiobook metadata from your Audiobookshelf library.</p>
            <div className="system-form-toggle">
                <input type="checkbox" id="absEnabledToggle" checked={enabled} onChange={e => setEnabled(e.target.checked)} />
                <div>
                    <label htmlFor="absEnabledToggle" className="system-form-toggle-label">Enable ABS enrichment</label>
                    <p className="system-form-toggle-hint">Auto-enrich audiobooks during scans.</p>
                </div>
            </div>
            {enabled && (
                <div className="system-form-indent">
                    <div className="system-form-row">
                        <label className="system-form-label">ABS URL</label>
                        <input type="text" className="input" value={url} onChange={e => setUrl(e.target.value)}
                            placeholder="http://audiobookshelf:80" style={{ maxWidth: 360, width: '100%' }} />
                        <p className="system-form-hint">Internal Docker hostname or LAN IP.</p>
                    </div>
                    <div className="system-form-row">
                        <label className="system-form-label">API Token</label>
                        <div className="system-form-inline">
                            <input type="password" className="input" value={token} onChange={e => setToken(e.target.value)}
                                placeholder="Paste your ABS API token" style={{ flex: '1 1 240px' }} />
                            <button className="btn btn-secondary" onClick={handleTest} disabled={isTesting} style={{ whiteSpace: 'nowrap' }}>
                                {isTesting ? 'Testing…' : 'Test Connection'}
                            </button>
                        </div>
                        {testResult && <p className="system-form-hint" style={{ marginTop: 6 }}>{testResult.text}</p>}
                    </div>
                    <div className="system-form-row">
                        <label className="system-form-label">Audiobooks Path Prefix</label>
                        <input type="text" className="input" value={prefix} onChange={e => setPrefix(e.target.value)}
                            placeholder="/audiobooks" style={{ maxWidth: 240, width: '100%' }} />
                        <p className="system-form-hint">ABS container path for your library (e.g. <code>/audiobooks</code>).</p>
                    </div>
                </div>
            )}
            {msg && <div className={`alert alert-${msg.type}`} style={{ marginBottom: 12 }}>{msg.text}</div>}
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <button className="btn btn-primary" onClick={handleSave} disabled={loading}>{loading ? 'Saving…' : 'Save'}</button>
                {enabled && (
                    <button className="btn btn-secondary" onClick={handleEnrichAll} disabled={isEnriching}>
                        {isEnriching ? 'Enriching…' : 'Re-enrich All from ABS'}
                    </button>
                )}
            </div>
        </>
    )
}

/* ── DetailedBreakdown ─────────────────────────────────────────────── */
function DetailedBreakdown({ stats }) {
    if (!stats) return null
    const rows = [
        { label: 'Ebooks', used: stats.ebook_used_human, total: stats.ebook_total_human, free: stats.ebook_free_human,
          percent: stats.ebook_total_bytes > 0 ? stats.ebook_used_bytes / stats.ebook_total_bytes * 100 : 0 },
        { label: 'Audiobooks', used: stats.audiobook_used_human, total: stats.audiobook_total_human, free: stats.audiobook_free_human,
          percent: stats.audiobook_total_bytes > 0 ? stats.audiobook_used_bytes / stats.audiobook_total_bytes * 100 : 0 },
        { label: 'Data & DB', used: stats.app_data_used_human, total: stats.app_data_total_human, free: stats.app_data_free_human,
          percent: stats.app_data_total_bytes > 0 ? stats.app_data_used_bytes / stats.app_data_total_bytes * 100 : 0 },
    ]
    return (
        <table className="data-table">
            <thead>
                <tr>
                    <th>Category</th>
                    <th>Used</th>
                    <th>Capacity</th>
                    <th>Free</th>
                    <th>% Used</th>
                </tr>
            </thead>
            <tbody>
                {rows.map(row => (
                    <tr key={row.label}>
                        <td>{row.label}</td>
                        <td style={{ fontWeight: 600 }}>{row.used}</td>
                        <td style={{ color: 'var(--text-secondary)' }}>{row.total}</td>
                        <td style={{ color: 'var(--success)' }}>{row.free} free</td>
                        <td>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                <div className="progress-bar" style={{ width: 60, height: 4 }}>
                                    <div className="progress-fill" style={{
                                        width: `${Math.min(100, row.percent)}%`,
                                        background: row.percent > 90 ? 'var(--error)' : row.percent > 75 ? 'var(--warning)' : 'var(--accent)'
                                    }} />
                                </div>
                                <span style={{ fontSize: '0.8rem' }}>{row.percent.toFixed(1)}%</span>
                            </div>
                        </td>
                    </tr>
                ))}
            </tbody>
        </table>
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
    const [forceDeleteConfirm, setForceDeleteConfirm] = useState(null) // null | { file } | 'all'

    useEffect(() => { loadFiles() }, [])

    const loadFiles = async () => {
        setLoading(true); setError(null)
        try { setFiles(await getUnsupportedFiles()) }
        catch (err) { setError(err.message) }
        finally { setLoading(false) }
    }

    const setFileBusy = (id, busy) => setBusyIds(prev => {
        const next = new Set(prev); busy ? next.add(id) : next.delete(id); return next
    })
    const setFileMsg = (id, msg) => setFileMessages(prev => ({ ...prev, [id]: msg }))

    const handleConvert = async (file, deleteSource) => {
        setFileBusy(file.id, true); setFileMsg(file.id, null)
        try {
            await convertUnsupportedFile(file.id, deleteSource)
            setFileMsg(file.id, { type: 'success', text: deleteSource ? 'Converted & deleted' : 'Converted to EPUB' })
            await loadFiles()
        } catch (err) { setFileMsg(file.id, { type: 'error', text: err.message }) }
        finally { setFileBusy(file.id, false) }
    }

    const handleDeleteSource = async (file) => {
        setFileBusy(file.id, true); setFileMsg(file.id, null)
        try { await deleteUnsupportedSource(file.id); await loadFiles() }
        catch (err) { setFileMsg(file.id, { type: 'error', text: err.message }) }
        finally { setFileBusy(file.id, false) }
    }

    const handleForceDelete = async (file) => {
        setFileBusy(file.id, true); setFileMsg(file.id, null); setForceDeleteConfirm(null)
        try { await forceDeleteUnsupportedFile(file.id); await loadFiles() }
        catch (err) { setFileMsg(file.id, { type: 'error', text: err.message }) }
        finally { setFileBusy(file.id, false) }
    }

    const handleForceDeleteAll = async () => {
        setBatchBusy(true); setBatchResult(null); setForceDeleteConfirm(null)
        try {
            const result = await forceDeleteAllUnsupportedFiles()
            setBatchResult({ type: 'success', text: `Force deleted ${result.total} file${result.total !== 1 ? 's' : ''}.` })
            await loadFiles()
        } catch (err) { setBatchResult({ type: 'error', text: err.message }) }
        finally { setBatchBusy(false) }
    }

    const handleBatchConvert = async (deleteSource) => {
        setBatchBusy(true); setBatchResult(null)
        try {
            const result = await convertAllUnsupportedFiles(deleteSource)
            const msg = `Converted ${result.succeeded.length} of ${result.total}.${result.failed.length > 0 ? ` ${result.failed.length} failed.` : ''}`
            setBatchResult({ type: result.failed.length > 0 ? 'error' : 'success', text: msg, detail: result })
            await loadFiles()
        } catch (err) { setBatchResult({ type: 'error', text: err.message }) }
        finally { setBatchBusy(false) }
    }

    const formatBytes = (bytes) => {
        if (!bytes) return '—'
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
        return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
    }

    const unconverted = files.filter(f => !f.already_converted)

    return (
        <div>
            <div style={{ display: 'flex', gap: 8, marginBottom: 16, flexWrap: 'wrap', alignItems: 'center' }}>
                <button className="btn btn-secondary" onClick={loadFiles} disabled={loading || batchBusy}>Refresh</button>
                {canAdmin && unconverted.length > 0 && (
                    <>
                        <button className="btn btn-primary" onClick={() => handleBatchConvert(false)} disabled={batchBusy}>
                            {batchBusy ? 'Converting…' : 'Convert All'}
                        </button>
                        <button className="btn btn-danger" onClick={() => handleBatchConvert(true)} disabled={batchBusy}>
                            {batchBusy ? 'Converting…' : 'Convert All & Delete Original'}
                        </button>
                    </>
                )}
                {canAdmin && files.length > 0 && (
                    <button className="btn btn-danger" onClick={() => setForceDeleteConfirm('all')} disabled={batchBusy}>
                        Force Delete All
                    </button>
                )}
            </div>

            {batchResult && (
                <div className={`alert alert-${batchResult.type}`} style={{ marginBottom: 16 }}>
                    {batchResult.text}
                    {batchResult.detail?.failed?.length > 0 && (
                        <ul style={{ marginTop: 8, paddingLeft: 20, fontSize: '0.8rem' }}>
                            {batchResult.detail.failed.map((f, i) => <li key={i}>{f.filename}: {f.error}</li>)}
                        </ul>
                    )}
                </div>
            )}

            <div className="system-card">
                <div className="system-card-header">
                    <h3>MOBI / AZW3 Files</h3>
                </div>
                <div className="system-card-body" style={{ paddingTop: 10, paddingBottom: 10 }}>
                    <p className="system-card-desc" style={{ marginBottom: 0 }}>
                        MOBI and AZW3 files cannot be read directly. Convert them to EPUB using Calibre or the built-in Python converter.
                    </p>
                </div>
                {loading ? (
                    <div style={{ padding: 32, textAlign: 'center' }}><div className="spinner" /></div>
                ) : error ? (
                    <div className="alert alert-error" style={{ margin: 16 }}>{error}</div>
                ) : files.length === 0 ? (
                    <div style={{ padding: 32, textAlign: 'center', color: 'var(--text-muted)' }}>No unsupported files found.</div>
                ) : (
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
                )}
            </div>

            {previewFile?.epub_ebook_id && (
                <EbookReader ebookId={previewFile.epub_ebook_id} bookTitle={previewFile.title || previewFile.filename} onClose={() => setPreviewFile(null)} />
            )}

            {forceDeleteConfirm && (
                <div className="modal-overlay" onClick={() => setForceDeleteConfirm(null)}>
                    <div className="modal-dialog" onClick={e => e.stopPropagation()}>
                        <h3 style={{ marginTop: 0 }}>Confirm Force Delete</h3>
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
                    </div>
                </div>
            )}
        </div>
    )
}

/* ── SystemPage ────────────────────────────────────────────────────── */
function SystemPage({ tab }) {
    const { hasMinRole } = useAuth()
    const canAdmin = hasMinRole('admin')
    const isMobile = useIsMobile()

    // Show unsupported files view vs main status view
    const [showUnsupported, setShowUnsupported] = useState(tab === 'unsupported')
    useEffect(() => { setShowUnsupported(tab === 'unsupported') }, [tab])

    // Synced collapsible state for paired cards
    const [libraryOpen, setLibraryOpen] = useState(false)
    const [absOpen, setAbsOpen] = useState(false)

    // Status data
    const [stats, setStats] = useState(null)
    const [counts, setCounts] = useState(null)
    const [unsupportedCount, setUnsupportedCount] = useState(null)
    const [statusLoading, setStatusLoading] = useState(true)
    const [statusError, setStatusError] = useState(null)

    const loadStatus = useCallback(async () => {
        setStatusLoading(true)
        setStatusError(null)
        try {
            const [diskData, ebooks, audiobooks, pairs, queue, unsupported] = await Promise.all([
                getDiskUsage(),
                getEbooks(),
                getAudiobooks(),
                getPairs(),
                getTranscriptionQueue(),
                getUnsupportedFiles(),
            ])
            setStats(diskData)
            setCounts({
                ebooks: ebooks.length,
                audiobooks: audiobooks.length,
                pairs: pairs.length,
                pendingQueue: queue.filter(q => q.status === 'pending').length,
                inProgressQueue: queue.filter(q => q.status === 'in_progress').length,
            })
            setUnsupportedCount(unsupported.length)
        } catch (err) {
            setStatusError('Failed to load system status')
            console.error(err)
        } finally {
            setStatusLoading(false)
        }
    }, [])

    useEffect(() => {
        if (!showUnsupported) loadStatus()
    }, [showUnsupported, loadStatus])

    const totalBooks = counts ? counts.ebooks + counts.audiobooks : 0
    const pairRate = counts ? Math.round(counts.pairs / Math.max(counts.ebooks, 1) * 100) : 0

    /* ── Unsupported Files view ── */
    if (showUnsupported) {
        return (
            <div>
                <div style={{ marginBottom: 20 }}>
                    <button className="btn btn-secondary" onClick={() => setShowUnsupported(false)}
                        style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="14" height="14">
                            <polyline points="15 18 9 12 15 6" />
                        </svg>
                        Back to System
                    </button>
                </div>
                <UnsupportedFilesTab canAdmin={canAdmin} />
            </div>
        )
    }

    /* ── Main Status view ── */
    return (
        <div>
            {/* Refresh button — top right */}
            <div className="system-toolbar">
                <div className="system-toolbar-right">
                    <button className="btn btn-secondary" onClick={loadStatus} disabled={statusLoading} style={{ fontSize: '0.8rem' }}>
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="14" height="14" style={{ marginRight: 4 }}>
                            <polyline points="23 4 23 10 17 10" /><polyline points="1 20 1 14 7 14" />
                            <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
                        </svg>
                        Refresh
                    </button>
                </div>
            </div>

            {statusLoading ? (
                <div className="loading-page"><div className="spinner" /></div>
            ) : statusError ? (
                <div className="alert alert-error">
                    {statusError}
                    <button className="btn btn-sm btn-secondary" onClick={loadStatus} style={{ marginLeft: 'auto' }}>Retry</button>
                </div>
            ) : (
                <>
                    {/* ── Section: System Status ── */}
                    <div className="system-section-header">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="18" height="18" className="system-section-icon">
                            <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
                        </svg>
                        <h3>System Status</h3>
                    </div>

                    {/* 4 stat badges */}
                    <div className="system-stat-grid">
                        <DiskUsageCard stats={stats} />
                        <StatBadgeCard
                            icon={<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="20" height="20"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" /><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" /></svg>}
                            color="blue"
                            label="Total Books"
                            value={totalBooks.toLocaleString()}
                            sub={`${counts.ebooks.toLocaleString()} ebooks · ${counts.audiobooks.toLocaleString()} audio`}
                        />
                        <StatBadgeCard
                            icon={<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="20" height="20"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" /><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" /></svg>}
                            color="purple"
                            label="Total Pairs"
                            value={counts.pairs.toLocaleString()}
                            sub={`${pairRate}% of ebooks paired`}
                        />
                        <StatBadgeCard
                            icon={<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="20" height="20"><circle cx="12" cy="12" r="10" /><polyline points="12 6 12 12 16 14" /></svg>}
                            color="amber"
                            label="Transcription Queue"
                            value={counts.pendingQueue}
                            sub={counts.inProgressQueue > 0 ? `${counts.inProgressQueue} processing now` : 'Queue is idle'}
                        />
                    </div>

                    {/* Calibre + Unsupported side by side */}
                    <div className="system-two-col" style={{ marginBottom: 24 }}>
                        <CalibreStatusCard />
                        <UnsupportedFilesCard
                            count={unsupportedCount}
                            onViewItems={() => setShowUnsupported(true)}
                        />
                    </div>

                    {/* Import Sources entry point */}
                    <div className="system-status-card" style={{ marginBottom: 24 }}>
                        <div className="system-status-card-left">
                            <div>
                                <h5 className="system-status-title">Import Sources</h5>
                                <p className="system-status-desc">
                                    Pull books from Audible, Google Play, and Nook.
                                </p>
                            </div>
                        </div>
                        <Link to="/system/import-sources" className="system-status-link">
                            CONFIGURE
                        </Link>
                    </div>

                    {/* Troubleshoot Library entry point */}
                    <div className="system-status-card" style={{ marginBottom: 24 }}>
                        <div className="system-status-card-left">
                            <div>
                                <h5 className="system-status-title">Troubleshoot Library</h5>
                                <p className="system-status-desc">
                                    Find and fix corrupt, encrypted, or missing files.
                                </p>
                            </div>
                        </div>
                        <Link to="/system/troubleshoot" className="system-status-link">
                            OPEN
                        </Link>
                    </div>

                    {/* ── Section: Configuration ── */}
                    <div className="system-section-header">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="18" height="18" className="system-section-icon">
                            <circle cx="12" cy="12" r="3" /><path d="M19.07 4.93a10 10 0 0 1 0 14.14M4.93 4.93a10 10 0 0 0 0 14.14" />
                        </svg>
                        <h3>Configuration</h3>
                    </div>

                    {/* Library + Transcription side by side — synced on desktop, independent on mobile */}
                    <div className="system-two-col">
                        {isMobile ? (
                            <>
                                <CollapsibleCard title="Library Settings">
                                    <SettingsSection />
                                </CollapsibleCard>
                                <CollapsibleCard title="Transcription Settings">
                                    <TranscriptionSettingsSection />
                                </CollapsibleCard>
                            </>
                        ) : (
                            <>
                                <CollapsibleCard title="Library Settings"
                                    open={libraryOpen} onToggle={() => setLibraryOpen(o => !o)}>
                                    <SettingsSection />
                                </CollapsibleCard>
                                <CollapsibleCard title="Transcription Settings"
                                    open={libraryOpen} onToggle={() => setLibraryOpen(o => !o)}>
                                    <TranscriptionSettingsSection />
                                </CollapsibleCard>
                            </>
                        )}
                    </div>

                    {/* ABS + Detailed Breakdown side by side — synced on desktop, independent on mobile */}
                    <div className="system-two-col">
                        {isMobile ? (
                            <>
                                <CollapsibleCard title="Audiobookshelf Integration">
                                    <ABSSettingsSection />
                                </CollapsibleCard>
                                <CollapsibleCard title="Detailed Disk Breakdown">
                                    <DetailedBreakdown stats={stats} />
                                </CollapsibleCard>
                            </>
                        ) : (
                            <>
                                <CollapsibleCard title="Audiobookshelf Integration"
                                    open={absOpen} onToggle={() => setAbsOpen(o => !o)}>
                                    <ABSSettingsSection />
                                </CollapsibleCard>
                                <CollapsibleCard title="Detailed Disk Breakdown"
                                    open={absOpen} onToggle={() => setAbsOpen(o => !o)}>
                                    <DetailedBreakdown stats={stats} />
                                </CollapsibleCard>
                            </>
                        )}
                    </div>

                    {/* ── Section: User Management (admin only) ── */}
                    {canAdmin && (
                        <>
                            <div className="system-section-header" style={{ marginTop: 8 }}>
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="18" height="18" className="system-section-icon">
                                    <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" />
                                    <path d="M23 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" />
                                </svg>
                                <h3>User Management</h3>
                            </div>
                            <UserManagementSection />
                        </>
                    )}
                </>
            )}
        </div>
    )
}

export default SystemPage
