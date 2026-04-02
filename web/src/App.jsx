import React, { useState, useEffect } from 'react'
import { Routes, Route, Navigate, Link, useLocation } from 'react-router-dom'
import { isLoggedIn, getMe, logout } from './api'
import { useTheme } from './ThemeContext'
import { DEFAULT_THEME } from './themes'
import { ThemePicker } from './components/ThemePicker'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import LoginPage from './pages/LoginPage'
import ChangePasswordPage from './pages/ChangePasswordPage'
import LibraryPage from './pages/LibraryPage'
import PairsPage from './pages/PairsPage'
import TranscriptionPage from './pages/TranscriptionPage'
import TranscriptionEditorPage from './pages/TranscriptionEditorPage'
import TranscriptionQueuePage from './pages/TranscriptionQueuePage'
import SystemPage from './pages/SystemPage'
import SeriesPage from './pages/SeriesPage'
import BookDetailPage from './pages/BookDetailPage'
import UnpairedPage from './pages/UnpairedPage'
import UserManagementPage from './pages/UserManagementPage'
import ContinuePage from './pages/ContinuePage'
import NewItemsPage from './pages/NewItemsPage'
import NewPairsPage from './pages/NewPairsPage'
import { AudioPlayerProvider, useAudioPlayer } from './contexts/AudioPlayerContext'
import { MiniPlayer } from './components/AudioPlayer'
import { AudioPlayerView } from './components/AudioPlayer'

function AppMiniPlayer() {
    const player = useAudioPlayer()
    const [showFullPlayer, setShowFullPlayer] = useState(false)

    if (!player.currentAudiobook) return null
    if (showFullPlayer) return <AudioPlayerView onClose={() => setShowFullPlayer(false)} />
    return <MiniPlayer onExpand={() => setShowFullPlayer(true)} />
}

function AppShell({ user, setUser }) {
    const location = useLocation()
    const { hasMinRole } = useAuth()

    const handleLogout = () => {
        logout()
        setUser(null)
    }

    const isSection = (prefix) => location.pathname.startsWith(prefix)
    const isExact = (path) => location.pathname === path
    const navClass = (prefix) => isSection(prefix) ? 'nav-link active' : 'nav-link'
    const subNavClass = (path) => isExact(path) ? 'nav-sub-link active' : 'nav-sub-link'

    const roleLabel = user.role
        ? user.role.charAt(0).toUpperCase() + user.role.slice(1)
        : 'User'

    return (
        <div className="app-layout">
            <aside className="sidebar">
                <div className="sidebar-logo">
                    <svg viewBox="0 0 560 360" width="90" height="58" style={{ flexShrink: 0 }}>
                        <defs>
                            <clipPath id="bm-logo">
                                <path d="M 30,20 H 530 V 295 L 280,350 L 30,295 Z"/>
                            </clipPath>
                        </defs>
                        <path d="M 30,20 H 530 V 295 L 280,350 L 30,295 Z" fill="var(--bg-card)" stroke="var(--border-light)" strokeWidth="6"/>
                        <g clipPath="url(#bm-logo)">
                            <rect x="55"  y="48"  width="180" height="10" rx="5" fill="var(--accent)"/>
                            <rect x="55"  y="70"  width="150" height="10" rx="5" fill="var(--accent)"/>
                            <rect x="55"  y="92"  width="170" height="10" rx="5" fill="var(--accent)"/>
                            <rect x="55"  y="114" width="140" height="10" rx="5" fill="var(--accent)"/>
                            <rect x="55"  y="136" width="165" height="10" rx="5" fill="var(--accent)"/>
                            <rect x="55"  y="158" width="145" height="10" rx="5" fill="var(--accent)"/>
                            <rect x="55"  y="180" width="160" height="10" rx="5" fill="var(--accent)"/>
                            <rect x="55"  y="202" width="135" height="10" rx="5" fill="var(--accent)"/>
                            <rect x="55"  y="224" width="155" height="10" rx="5" fill="var(--accent)"/>
                            <rect x="55"  y="246" width="130" height="10" rx="5" fill="var(--accent)"/>
                            <rect x="55"  y="268" width="148" height="10" rx="5" fill="var(--accent)"/>
                        </g>
                        <rect x="276" y="20" width="8" height="330" fill="var(--bg-primary)"/>
                        <g clipPath="url(#bm-logo)">
                            <rect x="295" y="108" width="22" height="100" rx="11" fill="var(--accent-secondary)"/>
                            <rect x="323" y="73"  width="22" height="170" rx="11" fill="var(--accent-secondary)"/>
                            <rect x="351" y="98"  width="22" height="120" rx="11" fill="var(--accent-secondary)"/>
                            <rect x="379" y="58"  width="22" height="200" rx="11" fill="var(--accent-secondary)"/>
                            <rect x="407" y="83"  width="22" height="150" rx="11" fill="var(--accent-secondary)"/>
                            <rect x="435" y="113" width="22" height="90"  rx="11" fill="var(--accent-secondary)"/>
                            <rect x="463" y="98"  width="22" height="120" rx="11" fill="var(--accent-secondary)"/>
                        </g>
                    </svg>
                    <div>
                        <h1>Tandem</h1>
                        <span>Audio &amp; Text Synchronizer</span>
                    </div>
                </div>
                <nav>
                    {/* Continue */}
                    <Link to="/continue" className={isExact('/continue') ? 'nav-link active' : 'nav-link'}>
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polygon points="5 3 19 12 5 21 5 3" /></svg>
                        Continue
                    </Link>

                    {/* Library */}
                    <Link to="/library/ebooks" className={navClass('/library')}>
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" /><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" /></svg>
                        Library
                    </Link>
                    {isSection('/library') && (
                        <div className="nav-sub-group">
                            <Link to="/library/ebooks" className={subNavClass('/library/ebooks')}>📚 Ebooks</Link>
                            <Link to="/library/audiobooks" className={subNavClass('/library/audiobooks')}>🎧 Audiobooks</Link>
                            <Link to="/library/new-items" className={subNavClass('/library/new-items')}>🆕 New Items</Link>
                        </div>
                    )}

                    {/* Series */}
                    <Link to="/series" className={navClass('/series')}>
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" /><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" /><path d="M12 2v20" /></svg>
                        Series
                    </Link>

                    {/* Book Pairs */}
                    <Link to="/pairs/paired" className={navClass('/pairs')}>
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M16 3h5v5" /><path d="M4 20L21 3" /><path d="M21 16v5h-5" /><path d="M15 15l6 6" /><path d="M4 4l5 5" /></svg>
                        Book Pairs
                    </Link>
                    {isSection('/pairs') && (
                        <div className="nav-sub-group">
                            <Link to="/pairs/paired" className={subNavClass('/pairs/paired')}>🔗 Paired Files</Link>
                            <Link to="/pairs/unpaired" className={subNavClass('/pairs/unpaired')}>🔀 Unpaired Items</Link>
                            <Link to="/pairs/new-pairs" className={subNavClass('/pairs/new-pairs')}>🆕 New Pairs</Link>
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
                            <Link to="/transcription/queue" className={subNavClass('/transcription/queue')}>📋 Queue</Link>
                            <Link to="/transcription/in-progress" className={subNavClass('/transcription/in-progress')}>⏳ In Progress</Link>
                            <Link to="/transcription/transcribed" className={subNavClass('/transcription/transcribed')}>✅ Transcribed</Link>
                        </div>
                    )}

                    {/* System */}
                    <Link to="/system/status" className={navClass('/system')}>
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="2" y="3" width="20" height="14" rx="2" ry="2" /><line x1="8" y1="21" x2="16" y2="21" /><line x1="12" y1="17" x2="12" y2="21" /></svg>
                        System
                    </Link>
                    {isSection('/system') && (
                        <div className="nav-sub-group">
                            <Link to="/system/status" className={subNavClass('/system/status')}>⚙️ Status</Link>
                            <Link to="/system/unsupported" className={subNavClass('/system/unsupported')}>⚠️ Unsupported Files</Link>
                        </div>
                    )}

                    {/* Users — admin/superadmin only */}
                    {hasMinRole('admin') && (
                        <Link to="/admin/users" className={navClass('/admin/users')}>
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M23 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" /></svg>
                            Users
                        </Link>
                    )}
                </nav>
                <div className="sidebar-footer">
                    <div className="sidebar-user">
                        <div className="avatar">{user.username[0].toUpperCase()}</div>
                        <div className="info">
                            <div className="name">{user.username}</div>
                            <div className="role">{roleLabel}</div>
                        </div>
                        <button className="btn btn-icon btn-secondary" onClick={handleLogout} title="Logout">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="16" height="16"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><polyline points="16 17 21 12 16 7" /><line x1="21" y1="12" x2="9" y2="12" /></svg>
                        </button>
                    </div>
                    <ThemePicker />
                </div>
            </aside>
            <main className="main-content">
                <Routes>
                    {/* Continue */}
                    <Route path="/continue" element={<ContinuePage />} />

                    {/* Library routes */}
                    <Route path="/library/ebooks" element={<LibraryPage tab="ebooks" />} />
                    <Route path="/library/audiobooks" element={<LibraryPage tab="audiobooks" />} />
                    <Route path="/library/new-items" element={<NewItemsPage />} />
                    <Route path="/pairs/new-pairs" element={<NewPairsPage />} />

                    {/* Series routes */}
                    <Route path="/series" element={<SeriesPage />} />

                    {/* Book Pairs routes */}
                    <Route path="/pairs/paired" element={<PairsPage tab="paired" />} />
                    <Route path="/pairs/unpaired" element={<UnpairedPage />} />
                    <Route path="/pairs/unpaired-books" element={<Navigate to="/pairs/unpaired" replace />} />
                    <Route path="/pairs/unpaired-audiobooks" element={<Navigate to="/pairs/unpaired" replace />} />

                    {/* Transcription routes */}
                    <Route path="/transcription/not-transcribed" element={<TranscriptionPage tab="not-transcribed" />} />
                    <Route path="/transcription/in-progress" element={<TranscriptionPage tab="in-progress" />} />
                    <Route path="/transcription/transcribed" element={<TranscriptionPage tab="transcribed" />} />
                    <Route path="/transcription/queue" element={<TranscriptionQueuePage />} />
                    <Route path="/transcription/edit/:pairId" element={<TranscriptionEditorPage />} />

                    {/* System */}
                    <Route path="/system/status" element={<SystemPage tab="status" />} />
                    <Route path="/system/unsupported" element={<SystemPage tab="unsupported" />} />
                    <Route path="/system" element={<Navigate to="/system/status" replace />} />

                    {/* Book Detail */}
                    <Route path="/book/:type/:id" element={<BookDetailPage />} />

                    {/* Admin routes */}
                    {hasMinRole('admin') && (
                        <Route path="/admin/users" element={<UserManagementPage />} />
                    )}

                    {/* Redirects */}
                    <Route path="/" element={<Navigate to="/continue" replace />} />
                    <Route path="/library" element={<Navigate to="/library/ebooks" replace />} />
                    <Route path="/pairs" element={<Navigate to="/pairs/paired" replace />} />
                    <Route path="/transcription" element={<Navigate to="/transcription/not-transcribed" replace />} />
                    <Route path="*" element={<Navigate to="/continue" replace />} />
                </Routes>
            </main>
            <AppMiniPlayer />
        </div>
    )
}

function App() {
    const [user, setUser] = useState(null)
    const [loading, setLoading] = useState(true)
    const { setTheme } = useTheme()

    useEffect(() => {
        if (isLoggedIn()) {
            getMe().then(u => {
                if (u) setTheme(u.theme || DEFAULT_THEME)
                setUser(u)
                setLoading(false)
            }).catch(() => {
                setLoading(false)
            })
        } else {
            setLoading(false)
        }
    }, []) // eslint-disable-line react-hooks/exhaustive-deps

    if (loading) {
        return (
            <div className="loading-page">
                <div className="spinner"></div>
                <span>Loading Tandem...</span>
            </div>
        )
    }

    if (!user) {
        return <LoginPage onLogin={(u) => {
            setTheme(u.theme || DEFAULT_THEME)
            setUser(u)
        }} />
    }

    // Force password reset before entering the app
    if (user.must_reset_password) {
        return <ChangePasswordPage onPasswordChanged={setUser} />
    }

    return (
        <AuthProvider user={user}>
            <AudioPlayerProvider>
                <AppShell user={user} setUser={setUser} />
            </AudioPlayerProvider>
        </AuthProvider>
    )
}

export default App
