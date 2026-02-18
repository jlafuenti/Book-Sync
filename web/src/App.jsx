import React, { useState, useEffect } from 'react'
import { Routes, Route, Navigate, Link, useLocation } from 'react-router-dom'
import { isLoggedIn, getMe, logout } from './api'
import LoginPage from './pages/LoginPage'
import LibraryPage from './pages/LibraryPage'
import PairsPage from './pages/PairsPage'
import TranscriptionPage from './pages/TranscriptionPage'

function App() {
    const [user, setUser] = useState(null)
    const [loading, setLoading] = useState(true)
    const location = useLocation()

    useEffect(() => {
        if (isLoggedIn()) {
            getMe().then(u => {
                setUser(u)
                setLoading(false)
            }).catch(() => {
                setLoading(false)
            })
        } else {
            setLoading(false)
        }
    }, [])

    if (loading) {
        return (
            <div className="loading-page">
                <div className="spinner"></div>
                <span>Loading BookSync...</span>
            </div>
        )
    }

    if (!user) {
        return <LoginPage onLogin={setUser} />
    }

    const handleLogout = () => {
        logout()
        setUser(null)
    }

    const isActive = (path) => location.pathname === path ? 'nav-link active' : 'nav-link'

    return (
        <div className="app-layout">
            <aside className="sidebar">
                <div className="sidebar-logo">
                    <h1>📖 BookSync</h1>
                    <span>Audio &amp; Text Synchronizer</span>
                </div>
                <nav>
                    <Link to="/" className={isActive('/')}>
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" /><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" /></svg>
                        Library
                    </Link>
                    <Link to="/pairs" className={isActive('/pairs')}>
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M16 3h5v5" /><path d="M4 20L21 3" /><path d="M21 16v5h-5" /><path d="M15 15l6 6" /><path d="M4 4l5 5" /></svg>
                        Book Pairs
                    </Link>
                    <Link to="/transcription" className={isActive('/transcription')}>
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" /><path d="M19 10v2a7 7 0 0 1-14 0v-2" /><line x1="12" y1="19" x2="12" y2="23" /><line x1="8" y1="23" x2="16" y2="23" /></svg>
                        Transcription
                    </Link>
                </nav>
                <div className="sidebar-footer">
                    <div className="sidebar-user">
                        <div className="avatar">{user.username[0].toUpperCase()}</div>
                        <div className="info">
                            <div className="name">{user.username}</div>
                            <div className="role">{user.is_admin ? 'Admin' : 'User'}</div>
                        </div>
                        <button className="btn btn-icon btn-secondary" onClick={handleLogout} title="Logout">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="16" height="16"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><polyline points="16 17 21 12 16 7" /><line x1="21" y1="12" x2="9" y2="12" /></svg>
                        </button>
                    </div>
                </div>
            </aside>
            <main className="main-content">
                <Routes>
                    <Route path="/" element={<LibraryPage />} />
                    <Route path="/pairs" element={<PairsPage />} />
                    <Route path="/transcription" element={<TranscriptionPage />} />
                    <Route path="*" element={<Navigate to="/" />} />
                </Routes>
            </main>
        </div>
    )
}

export default App
