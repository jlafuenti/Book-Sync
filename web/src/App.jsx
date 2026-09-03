import React, { useState, useEffect } from 'react'
import { Routes, Route, Navigate, Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { isLoggedIn, getMe, logout, getUsers } from './api'
import { useTheme } from './ThemeContext'
import { DEFAULT_THEME } from './themes'
import { ThemePicker } from './components/ThemePicker'
import { RequireRole } from './components/RequireRole'
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
import HomePage from './pages/HomePage'
import NewItemsPage from './pages/NewItemsPage'
import NewPairsPage from './pages/NewPairsPage'
import ImportSourcesPage from './pages/ImportSourcesPage'
import TroubleshootPage from './pages/TroubleshootPage'
import { AudioPlayerProvider, useAudioPlayer } from './contexts/AudioPlayerContext'
import { MiniPlayer } from './components/AudioPlayer'
import { AudioPlayerView } from './components/AudioPlayer'
import useIsMobile from './hooks/useIsMobile'
import BottomNavBar from './components/BottomNavBar'
import MobileTopBar from './components/MobileTopBar'
import MobileDrawer from './components/MobileDrawer'

function AppMiniPlayer() {
    const player = useAudioPlayer()
    const [showFullPlayer, setShowFullPlayer] = useState(false)

    if (!player.currentAudiobook) return null
    if (showFullPlayer) return <AudioPlayerView onClose={() => setShowFullPlayer(false)} />
    return <MiniPlayer onExpand={() => setShowFullPlayer(true)} />
}

const FILTERABLE_PATHS = ['/library', '/series', '/transcription']
const SEARCH_HIDDEN_PATHS = ['/system', '/admin']

function GlobalSearchBar() {
    const navigate = useNavigate()
    const location = useLocation()
    const [query, setQuery] = useState('')
    const [searchParams, setSearchParams] = useSearchParams()

    const isHidden = SEARCH_HIDDEN_PATHS.some(p => location.pathname.startsWith(p))
    const isFilterable = FILTERABLE_PATHS.some(p => location.pathname.startsWith(p))
    const urlSearch = searchParams.get('search') || ''

    // Keep the input in sync with the URL param so chip-✕ clears the global bar too
    useEffect(() => {
        if (isFilterable) setQuery(urlSearch)
        else setQuery('')
    }, [urlSearch, isFilterable]) // eslint-disable-line react-hooks/exhaustive-deps

    const handleChange = (e) => {
        const val = e.target.value
        setQuery(val)
        if (isFilterable) {
            setSearchParams(val ? { search: val } : {}, { replace: true })
        }
    }

    const handleSubmit = (e) => {
        e.preventDefault()
        const q = query.trim()
        if (!q) return
        if (isFilterable) {
            setSearchParams({ search: q }, { replace: true })
        } else {
            navigate(`/library?search=${encodeURIComponent(q)}`)
            setQuery('')
        }
    }

    if (isHidden) return null

    return (
        <form className="global-search-bar" onSubmit={handleSubmit}>
            <div className="global-search-wrap">
                <svg className="global-search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="16" height="16">
                    <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
                </svg>
                <input
                    type="text"
                    className="global-search-input"
                    placeholder="Search..."
                    value={query}
                    onChange={handleChange}
                />
            </div>
        </form>
    )
}

// Map route paths to mobile top bar titles
function getMobilePageTitle(pathname) {
    if (pathname === '/continue') return 'Home'
    if (pathname.startsWith('/library')) return 'Library'
    if (pathname.startsWith('/series')) return 'Series'
    if (pathname.startsWith('/pairs')) return 'Book Pairs'
    if (pathname.startsWith('/transcription/edit')) return 'Transcription Editor'
    if (pathname.startsWith('/transcription')) return 'Transcription'
    if (pathname.startsWith('/system')) return 'Admin Console'
    if (pathname.startsWith('/book/')) return 'Book Details'
    return 'Tandem'
}

export function AppShell({ user, setUser }) {
    const location = useLocation()
    const { hasMinRole } = useAuth()
    const [sidebarHovered, setSidebarHovered] = useState(false)
    const isMobile = useIsMobile()
    const [drawerOpen, setDrawerOpen] = useState(false)
    const navigate = useNavigate()

    const handleLogout = async () => {
        await logout()
        setUser(null)
    }

    // Pending registrations (issue #282). With ALLOW_PUBLIC_REGISTRATION on,
    // an account request otherwise sits unseen until someone happens to open
    // System -> Users; the count rides on the nav entry that leads there.
    //
    // Admin-only because GET /api/users/?filter=pending is: asking from an
    // editor's session would buy a 403 and nothing else. A failure is
    // swallowed — a missing badge is not worth an error on every page.
    const isAdmin = hasMinRole('admin')
    const [pendingUsers, setPendingUsers] = useState(0)
    useEffect(() => {
        if (!isAdmin) return
        let cancelled = false
        Promise.resolve()
            .then(() => getUsers('pending'))
            .then(list => { if (!cancelled) setPendingUsers(list.length) })
            .catch(() => {})
        return () => { cancelled = true }
    }, [isAdmin])

    const isSection = (prefix) => location.pathname.startsWith(prefix)
    const isExact = (path) => location.pathname === path
    const navClass = (prefix) => isSection(prefix) ? 'nav-link active' : 'nav-link'
    const subNavClass = (path) => isExact(path) ? 'nav-sub-link active' : 'nav-sub-link'

    const roleLabel = user.role
        ? user.role.charAt(0).toUpperCase() + user.role.slice(1)
        : 'User'

    // Determine if current page uses a back button instead of hamburger
    const isDetailPage = location.pathname.startsWith('/book/')
    const isEditorPage = location.pathname.startsWith('/transcription/edit')
    const useBackButton = isDetailPage || isEditorPage

    const mobileTitle = getMobilePageTitle(location.pathname)

    return (
        <div className="app-layout">
            {/* Mobile Navigation */}
            {isMobile && (
                <>
                    <MobileTopBar
                        title={mobileTitle}
                        onMenuOpen={() => setDrawerOpen(true)}
                        leftIcon={useBackButton ? 'back' : 'menu'}
                        onBack={() => navigate(-1)}
                    />
                    <MobileDrawer
                        open={drawerOpen}
                        onClose={() => setDrawerOpen(false)}
                        user={user}
                        onLogout={handleLogout}
                    />
                    <BottomNavBar />
                </>
            )}

            <aside
                className={sidebarHovered ? 'sidebar expanded' : 'sidebar'}
                onMouseEnter={() => setSidebarHovered(true)}
                onMouseLeave={() => setSidebarHovered(false)}
            >
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
                    <div className="sidebar-logo-text">
                        <h1>Tandem</h1>
                        <span>Audio &amp; Text Synchronizer</span>
                    </div>
                </div>
                <nav>
                    {/* Home */}
                    <Link to="/continue" className={isExact('/continue') ? 'nav-link active' : 'nav-link'} title="Home">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /><polyline points="9 22 9 12 15 12 15 22" /></svg>
                        <span>Home</span>
                    </Link>

                    {/* Library */}
                    <Link to="/library" className={navClass('/library')} title="Library">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" /><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" /></svg>
                        <span>Library</span>
                    </Link>

                    {/* Series */}
                    <Link to="/series" className={navClass('/series')} title="Series">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" /><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" /><path d="M12 2v20" /></svg>
                        <span>Series</span>
                    </Link>

                    {/* Transcription */}
                    <Link to="/transcription" className={navClass('/transcription')} title="Transcription">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" /><path d="M19 10v2a7 7 0 0 1-14 0v-2" /><line x1="12" y1="19" x2="12" y2="23" /><line x1="8" y1="23" x2="16" y2="23" /></svg>
                        <span>Transcription</span>
                    </Link>

                    {/* System — visible from editor up, because Troubleshoot and
                        Unsupported are editor-level. The target differs by role:
                        pointing an editor at /system/status would land them on a
                        redirect, which reads as a broken app rather than as a
                        permission boundary (issue #283). */}
                    {hasMinRole('editor') && (
                    <Link to={hasMinRole('admin') ? '/system/status' : '/system/troubleshoot'} className={navClass('/system')} title="System">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="2" y="3" width="20" height="14" rx="2" ry="2" /><line x1="8" y1="21" x2="16" y2="21" /><line x1="12" y1="17" x2="12" y2="21" /></svg>
                        <span>System</span>
                        {pendingUsers > 0 && (
                            <span
                                className="badge badge-pending"
                                data-testid="pending-users-badge"
                                title={`${pendingUsers} account request${pendingUsers !== 1 ? 's' : ''} waiting for approval`}
                                style={{ marginLeft: 'auto' }}
                            >
                                {pendingUsers}
                            </span>
                        )}
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
                <GlobalSearchBar />
                <Routes>
                    {/* Home */}
                    <Route path="/continue" element={<HomePage />} />

                    {/* Library routes */}
                    <Route path="/library" element={<LibraryPage />} />
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
                    <Route path="/transcription" element={<TranscriptionPage tab="not-transcribed" />} />
                    <Route path="/transcription/not-transcribed" element={<TranscriptionPage tab="not-transcribed" />} />
                    <Route path="/transcription/in-progress" element={<TranscriptionPage tab="in-progress" />} />
                    <Route path="/transcription/transcribed" element={<TranscriptionPage tab="transcribed" />} />
                    <Route path="/transcription/queue" element={<TranscriptionPage tab="queue" />} />
                    <Route path="/transcription/edit/:pairId" element={<TranscriptionEditorPage />} />

                    {/* System — role-gated (issue #283). Admin for the
                        infrastructure views; editor for the two library-
                        maintenance ones, whose fix controls are already
                        editor-level. The server enforces the same split; this
                        only stops the console being presented to someone who
                        cannot use it. */}
                    <Route path="/system" element={<RequireRole min="admin"><SystemPage tab="status" /></RequireRole>} />
                    <Route path="/system/status" element={<RequireRole min="admin"><SystemPage tab="status" /></RequireRole>} />
                    <Route path="/system/unsupported" element={<RequireRole min="editor"><SystemPage tab="unsupported" /></RequireRole>} />
                    <Route path="/system/import-sources" element={<RequireRole min="admin"><ImportSourcesPage /></RequireRole>} />
                    <Route path="/system/troubleshoot" element={<RequireRole min="editor"><TroubleshootPage /></RequireRole>} />

                    {/* Book Detail */}
                    <Route path="/book/:type/:id" element={<BookDetailPage />} />

                    {/* Admin redirect — user management is now embedded in System */}
                    <Route path="/admin/users" element={<Navigate to="/system" replace />} />
                    <Route path="/admin/*" element={<Navigate to="/system" replace />} />

                    {/* Redirects */}
                    <Route path="/" element={<Navigate to="/continue" replace />} />
                    {/* /library is now a direct route, no redirect needed */}
                    <Route path="/pairs" element={<Navigate to="/pairs/paired" replace />} />
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

    // The flag is only read once, at mount (issue #209). api.js announces any
    // later 403 password_reset_required; setting the flag here reuses the gate
    // below rather than introducing a second one. Backstop for a call that races
    // the mount-time getMe(), not for an admin resetting a live session — that
    // path bumps token_version too and surfaces as a 401.
    useEffect(() => {
        const onGated = () => setUser(u => (u && !u.must_reset_password
            ? { ...u, must_reset_password: true }
            : u))
        window.addEventListener('tandem:password-reset-required', onGated)
        return () => window.removeEventListener('tandem:password-reset-required', onGated)
    }, [])

    // The session ended and could not be refreshed (issue #268). api.js used to
    // handle this itself with `window.location.href = '/login'` — a full reload,
    // fired from wherever the failing request happened to be, including a
    // background position heartbeat. That tore down an open reader along with
    // its pending save. Dropping `user` unmounts to LoginPage through React
    // instead, so anything mid-flight gets to finish.
    useEffect(() => {
        const onUnauthorized = () => setUser(null)
        window.addEventListener('tandem:unauthorized', onUnauthorized)
        return () => window.removeEventListener('tandem:unauthorized', onUnauthorized)
    }, [])

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
