import React, { useState, useEffect } from 'react'
import { getDiskUsage, getSettings, updateSettings } from '../api'

function SystemPage() {
    const [stats, setStats] = useState(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)

    useEffect(() => {
        loadStats()
    }, [])

    const loadStats = async () => {
        setLoading(true)
        try {
            const data = await getDiskUsage()
            setStats(data)
            setError(null)
        } catch (err) {
            console.error(err)
            setError('Failed to load system statistics')
        } finally {
            setLoading(false)
        }
    }

    if (loading) {
        return (
            <div className="loading-page">
                <div className="spinner"></div>
            </div>
        )
    }

    if (error) {
        return (
            <div className="alert alert-error">
                {error}
                <button className="btn btn-sm btn-secondary" onClick={loadStats} style={{ marginLeft: 'auto' }}>Retry</button>
            </div>
        )
    }

    return (
        <div className="page-wrapper">
            <div className="page-header">
                <h2>System Status</h2>
                <p>Monitor resource usage and system health.</p>
            </div>

            <div className="stat-grid">
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

            <SettingsSection />

            <div className="card">
                <div className="card-header">
                    <h3>Detailed Breakdown</h3>
                </div>
                <div className="table-wrapper">
                    <table>
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
            </div>
        </div>
    )
}

function SettingsSection() {
    const [ebookPatterns, setEbookPatterns] = useState('')
    const [audiobookPatterns, setAudiobookPatterns] = useState('')
    const [loading, setLoading] = useState(false)
    const [msg, setMsg] = useState(null)

    useEffect(() => {
        loadSettings()
    }, [])

    const loadSettings = async () => {
        try {
            const settings = await getSettings()
            if (settings.ebook_filename_patterns) {
                setEbookPatterns(settings.ebook_filename_patterns.join('\n'))
            }
            if (settings.audiobook_filename_patterns) {
                setAudiobookPatterns(settings.audiobook_filename_patterns.join('\n'))
            }
        } catch (err) {
            console.error(err)
        }
    }

    const handleSave = async () => {
        setLoading(true)
        setMsg(null)
        try {
            const ebList = ebookPatterns.split('\n').filter(p => p.trim() !== '')
            const abList = audiobookPatterns.split('\n').filter(p => p.trim() !== '')

            await updateSettings({
                ebook_filename_patterns: ebList,
                audiobook_filename_patterns: abList
            })
            setMsg({ type: 'success', text: 'Settings saved successfully' })
        } catch (err) {
            console.error(err)
            setMsg({ type: 'error', text: 'Failed to save settings' })
        } finally {
            setLoading(false)
        }
    }

    return (
        <div className="card" style={{ marginBottom: '24px' }}>
            <div className="card-header">
                <h3>Library Settings</h3>
            </div>
            <div style={{ padding: '16px' }}>
                <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '16px' }}>
                    Enter one pattern per line. Tokens: <code>&lt;Author&gt;</code>, <code>&lt;Series&gt;</code>, <code>&lt;Book Number&gt;</code>, <code>&lt;Title&gt;</code>.
                </p>

                <div style={{ marginBottom: '24px' }}>
                    <label style={{ display: 'block', marginBottom: '8px', fontWeight: 600 }}>
                        EBook Filename Patterns
                    </label>
                    <textarea
                        className="input"
                        rows={5}
                        value={ebookPatterns}
                        onChange={e => setEbookPatterns(e.target.value)}
                        placeholder="<Author> - [<Series> <Book Number>] - <Title>"
                        style={{ fontFamily: 'monospace', width: '100%' }}
                    />
                </div>

                <div style={{ marginBottom: '24px' }}>
                    <label style={{ display: 'block', marginBottom: '8px', fontWeight: 600 }}>
                        Audiobook Filename Patterns
                    </label>
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

                <button
                    className="btn btn-primary"
                    onClick={handleSave}
                    disabled={loading}
                >
                    {loading ? 'Saving...' : 'Save Settings'}
                </button>
            </div>
        </div>
    )
}

export default SystemPage

function StatCard({ title, icon, color, usedHuman, totalHuman, usedBytes, totalBytes }) {
    const percent = totalBytes > 0 ? Math.min(100, (usedBytes / totalBytes) * 100) : 0

    // Icons
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
        <div className="stat-card" style={{ display: 'block' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '16px', marginBottom: '16px' }}>
                <div className={`stat-icon ${color}`}>
                    {icons[icon]}
                </div>
                <div>
                    <div className="stat-value">{usedHuman}</div>
                    <div className="stat-label">Used of {totalHuman}</div>
                </div>
            </div>

            <div className="progress-bar" style={{ height: '8px', background: 'var(--bg-input)' }}>
                <div
                    className="progress-fill"
                    style={{
                        width: `${percent}%`,
                        background: percent > 90 ? 'var(--error)' : percent > 75 ? 'var(--warning)' : 'var(--accent)'
                    }}
                ></div>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '8px', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                <span>{percent.toFixed(1)}% Used</span>
            </div>
        </div>
    )
}

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
                        ></div>
                    </div>
                    <span style={{ fontSize: '0.8rem' }}>{percent.toFixed(1)}%</span>
                </div>
            </td>
        </tr>
    )
}
