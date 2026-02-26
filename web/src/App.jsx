import React, { useState, useEffect } from 'react'
import { Routes, Route, Navigate, Link, useLocation } from 'react-router-dom'
import { isLoggedIn, getMe, logout } from './api'
import LoginPage from './pages/LoginPage'
import LibraryPage from './pages/LibraryPage'
import PairsPage from './pages/PairsPage'
import TranscriptionPage from './pages/TranscriptionPage'
import TranscriptionEditorPage from './pages/TranscriptionEditorPage'
import SystemPage from './pages/SystemPage'
import SeriesPage from './pages/SeriesPage'

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

    // Check if current path starts with a given prefix
    const isSection = (prefix) => location.pathname.startsWith(prefix)
    const isExact = (path) => location.pathname === path

    // Main nav link class — active if any sub-route matches
    const navClass = (prefix) => isSection(prefix) ? 'nav-link active' : 'nav-link'

    // Sub-nav link class
    const subNavClass = (path) => isExact(path) ? 'nav-sub-link active' : 'nav-sub-link'

    return (
        <div className="app-layout">
            <aside className="sidebar">
                <div className="sidebar-logo">
                    <h1>📖 BookSync</h1>
                    <span>Audio &amp; Text Synchronizer</span>
                </div>
                <nav>
                    {/* Library */}
                    <Link to="/library/ebooks" className={navClass('/library')}>
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" /><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" /></svg>
                        Library
                    </Link>
                    {isSection('/library') && (
                        <div className="nav-sub-group">
                            <Link to="/library/ebooks" className={subNavClass('/library/ebooks')}>📚 Ebooks</Link>
                            <Link to="/library/audiobooks" className={subNavClass('/library/audiobooks')}>🎧 Audiobooks</Link>
                            <Link to="/library/series" className={subNavClass('/library/series')}>📖 Series</Link>
                        </div>
                    )}

                    {/* Book Pairs */}
                    <Link to="/pairs/paired" className={navClass('/pairs')}>
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M16 3h5v5" /><path d="M4 20L21 3" /><path d="M21 16v5h-5" /><path d="M15 15l6 6" /><path d="M4 4l5 5" /></svg>
                        Book Pairs
                    </Link>
                    {isSection('/pairs') && (
                        <div className="nav-sub-group">
                            <Link to="/pairs/paired" className={subNavClass('/pairs/paired')}>🔗 Paired Files</Link>
                            <Link to="/pairs/unpaired-books" className={subNavClass('/pairs/unpaired-books')}>📚 Unpaired Books</Link>
                            <Link to="/pairs/unpaired-audiobooks" className={subNavClass('/pairs/unpaired-audiobooks')}>🎧 Unpaired Audiobooks</Link>
                        </div>
                    )}

                    {/* Transcription */}
                    <Link to="/transcription/not-transcribed" className={navClass('/transcription')}>
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" /><path d="M19 10v2a7 7 0 0 1-14 0v-2" /><line x1="12" y1="19" x2="12" y2="23" /><line x1="8" y1="23" x2="16" y2="23" /></svg>
                        Transcription
                    </Link>
                    {isSection('/transcription') && (
                        <div className="nav-sub-group">
                            <Link to="/transcription/not-transcribed" className={subNavClass('/transcription/not-transcribed')}>⏸️ Not Transcribed</Link>
                            <Link to="/transcription/in-progress" className={subNavClass('/transcription/in-progress')}>⏳ In Progress</Link>
                            <Link to="/transcription/transcribed" className={subNavClass('/transcription/transcribed')}>✅ Transcribed</Link>
                        </div>
                    )}

                    {/* System */}
                    <Link to="/system" className={isExact('/system') ? 'nav-link active' : 'nav-link'}>
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="2" y="3" width="20" height="14" rx="2" ry="2" /><line x1="8" y1="21" x2="16" y2="21" /><line x1="12" y1="17" x2="12" y2="21" /></svg>
                        System
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
                    {/* Library routes */}
                    <Route path="/library/ebooks" element={<LibraryPage tab="ebooks" />} />
                    <Route path="/library/audiobooks" element={<LibraryPage tab="audiobooks" />} />
                    <Route path="/library/series" element={<SeriesPage />} />

                    {/* Book Pairs routes */}
                    <Route path="/pairs/paired" element={<PairsPage tab="paired" />} />
                    <Route path="/pairs/unpaired-books" element={<PairsPage tab="unpaired-books" />} />
                    <Route path="/pairs/unpaired-audiobooks" element={<PairsPage tab="unpaired-audiobooks" />} />

                    {/* Transcription routes */}
                    <Route path="/transcription/not-transcribed" element={<TranscriptionPage tab="not-transcribed" />} />
                    <Route path="/transcription/in-progress" element={<TranscriptionPage tab="in-progress" />} />
                    <Route path="/transcription/transcribed" element={<TranscriptionPage tab="transcribed" />} />
                    <Route path="/transcription/edit/:pairId" element={<TranscriptionEditorPage />} />

                    {/* System */}
                    <Route path="/system" element={<SystemPage />} />

                    {/* Redirects */}
                    <Route path="/" element={<Navigate to="/library/ebooks" replace />} />
                    <Route path="/library" element={<Navigate to="/library/ebooks" replace />} />
                    <Route path="/pairs" element={<Navigate to="/pairs/paired" replace />} />
                    <Route path="/transcription" element={<Navigate to="/transcription/not-transcribed" replace />} />
                    <Route path="*" element={<Navigate to="/library/ebooks" replace />} />
                </Routes>
            </main>
        </div>
    )
}

export default App
