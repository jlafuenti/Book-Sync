import React, { useState, useEffect } from 'react'
import { getDiskUsage } from '../api'

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
                <div className="stat-card">
                    <div className="stat-icon purple">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="24" height="24">
                            <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
                            <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
                        </svg>
                    </div>
                    <div>
                        <div className="stat-value">{stats.ebook_dir_human}</div>
                        <div className="stat-label">EBook Library</div>
                    </div>
                </div>

                <div className="stat-card">
                    <div className="stat-icon green">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="24" height="24">
                            <circle cx="12" cy="12" r="10" />
                            <polygon points="10 8 16 12 10 16 10 8" />
                        </svg>
                    </div>
                    <div>
                        <div className="stat-value">{stats.audiobook_dir_human}</div>
                        <div className="stat-label">Audiobook Library</div>
                    </div>
                </div>

                <div className="stat-card">
                    <div className="stat-icon blue">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="24" height="24">
                            <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" />
                            <polyline points="3.27 6.96 12 12.01 20.73 6.96" />
                            <line x1="12" y1="22.08" x2="12" y2="12" />
                        </svg>
                    </div>
                    <div>
                        <div className="stat-value">{stats.app_data_dir_human}</div>
                        <div className="stat-label">App Data & DB</div>
                    </div>
                </div>
            </div>

            <div className="card">
                <div className="card-header">
                    <h3>Detailed Breakdown</h3>
                </div>
                <div className="table-wrapper">
                    <table>
                        <thead>
                            <tr>
                                <th>Category</th>
                                <th>Size</th>
                                <th>Raw Bytes</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr>
                                <td>Ebooks</td>
                                <td>{stats.ebook_dir_human}</td>
                                <td>{stats.ebook_dir_bytes.toLocaleString()} bytes</td>
                            </tr>
                            <tr>
                                <td>Audiobooks</td>
                                <td>{stats.audiobook_dir_human}</td>
                                <td>{stats.audiobook_dir_bytes.toLocaleString()} bytes</td>
                            </tr>
                            <tr>
                                <td>Data & Database</td>
                                <td>{stats.app_data_dir_human}</td>
                                <td>{stats.app_data_dir_bytes.toLocaleString()} bytes</td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    )
}

export default SystemPage
