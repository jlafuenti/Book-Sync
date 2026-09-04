import React, { useState, useEffect, useCallback, lazy, Suspense } from 'react'
import { Routes, Route, Navigate, Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { isLoggedIn, getMe, logout, getUsers } from './api'
import { useTheme } from './ThemeContext'
import { DEFAULT_THEME } from './themes'
import { ThemePicker } from './components/ThemePicker'
import { RequireRole } from './components/RequireRole'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import LoginPage from './pages/LoginPage'
import ChangePasswordPage from './pages/ChangePasswordPage'
import TermsPage from './pages/TermsPage'
import LibraryPage from './pages/LibraryPage'
import PairsPage from './pages/PairsPage'
import SeriesPage from './pages/SeriesPage'
import UnpairedPage from './pages/UnpairedPage'
import HomePage from './pages/HomePage'
import NewItemsPage from './pages/NewItemsPage'
import NewPairsPage from './pages/NewPairsPage'
import { AudioPlayerProvider, useAudioPlayer } from './contexts/AudioPlayerContext'
import { MiniPlayer } from './components/AudioPlayer'
import { AudioPlayerView } from './components/AudioPlayer'
import useIsMobile from './hooks/useIsMobile'
import BottomNavBar from './components/BottomNavBar'
import MobileTopBar from './components/MobileTopBar'
import MobileDrawer from './components/MobileDrawer'
import SignOutEverywhere from './components/SignOutEverywhere'
import { switchToEbook } from './lib/handoff'

// Route-level code splitting (issue #281).
//
// The service worker precaches every built .js file and re-downloads it on
// every deploy (docs/web-pwa.md), so a single entry chunk charges every user —
// including one who only ever opens Home on a phone — for react-markdown, the
// transcription editor and the whole admin console. These are the routes that
// are both heavy and rarely the first thing anyone opens; each dynamic import()
// is what makes Rollup emit them as separate chunks, so hoisting one back up to
// a static import silently switches the split off. App.codeSplit.test.jsx pins
// that, and pins which pages deliberately stay eager: HomePage, LibraryPage and
// LoginPage are the first paint and must not cost a second request.
//
// epub.js is the one big dependency this cannot move: HomePage opens the reader
// too, so it is reachable from the eager tree. It gets a manual vendor chunk
// instead (src/build/manualChunks.js) — still downloaded up front, but no longer
// re-downloaded every time an eager page changes.
const TranscriptionPage = lazy(() => import('./pages/TranscriptionPage'))
const TranscriptionEditorPage = lazy(() => import('./pages/TranscriptionEditorPage'))
const SystemPage = lazy(() => import('./pages/SystemPage'))
const BookDetailPage = lazy(() => import('./pages/BookDetailPage'))
const ImportSourcesPage = lazy(() => import('./pages/ImportSourcesPage'))
const TroubleshootPage = lazy(() => import('./pages/TroubleshootPage'))

// Shown while a route chunk is in flight. Same markup as App's boot spinner;
// on a warm cache it is on screen for a frame or two.
function RouteFallback() {
    return (
        <div className="loading-page" data-testid="route-loading">
            <div className="spinner"></div>
            <span>Loading...</span>
        </div>
    )
}

// The player every route other than Home and BookDetail gets: it is mounted
// once in the shell, outside <Routes>. Until issue #267 it passed the full
// player no `onSwitchToEbook`, so the "Read" button — which renders only when
// that prop is set — was missing exactly where the mini-player is the usual way
// back in (mobile). Whether the audio->text handoff existed depended on which
// page you had started the book from; Android always offers it for a pair.
//
// Neither of the two pages that already had the handoff opens the reader by
// URL, so the shell cannot just navigate: it hands BookDetailPage the intent in
// router state, and that page opens its reader on arrival.
export function AppMiniPlayer() {
    const player = useAudioPlayer()
    const navigate = useNavigate()
    const [showFullPlayer, setShowFullPlayer] = useState(false)

    if (!player.currentAudiobook) return null
    if (showFullPlayer) {
        return (
            <AudioPlayerView
                onClose={() => setShowFullPlayer(false)}
                onSwitchToEbook={player.pairedEbookId ? async (pairId, ebookId) => {
                    const pos = await switchToEbook(player, pairId)
                    setShowFullPlayer(false)
                    navigate(`/book/ebook/${ebookId}`, {
                        state: { openReader: true, initialChapter: pos?.epub_chapter ?? null },
                    })
                } : null}
            />
        )
    }
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
                        onSignedOutEverywhere={() => setUser(null)}
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
                    {/* Issue #250: the account-wide revoke, for a lost device.
                        Separate from the icon above, which now ends only this
                        browser's session. Hidden while the sidebar is collapsed,
                        the same as the Logout button's own label. */}
                    <SignOutEverywhere
                        className="sidebar-signout-all"
                        onSignedOut={() => setUser(null)}
                    />
                    <ThemePicker />
                </div>
            </aside>
            <main className="main-content">
                <GlobalSearchBar />
                <Suspense fallback={<RouteFallback />}>
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
                </Suspense>
            </main>
            <AppMiniPlayer />
        </div>
    )
}

// Paths that render before anyone is asked to sign in. There is exactly one:
// the terms of use (issue #262), whose audience is the person looking at the
// registration form and who therefore has no session at all. Everything else in
// this app is behind the gate in App().
const PUBLIC_PATHS = { '/terms': <TermsPage /> }

function App() {
    const [user, setUser] = useState(null)
    const [loading, setLoading] = useState(true)
    const [unreachable, setUnreachable] = useState(false)
    const { setTheme } = useTheme()
    const { pathname } = useLocation()
    const publicPage = PUBLIC_PATHS[pathname]

    // Boot: decide between the app, the login form and the reset gate (#211).
    //
    // The two failures are not the same thing and must not look the same. A
    // rejected session (401) resolves to null and belongs on the login form.
    // A *rejected promise* means the request never got an answer — offline, DNS,
    // a proxy that dropped the connection — and this used to fall through to
    // the same bare login form, telling the user they had been signed out and
    // inviting them to retype a password that would not get through either.
    const bootstrap = useCallback(() => {
        if (!isLoggedIn()) {
            setLoading(false)
            return
        }
        setLoading(true)
        setUnreachable(false)
        getMe().then(u => {
            if (u) setTheme(u.theme || DEFAULT_THEME)
            setUser(u)
            setLoading(false)
        }).catch(() => {
            setUnreachable(true)
            setLoading(false)
        })
    }, []) // eslint-disable-line react-hooks/exhaustive-deps

    // Not on a public path: the terms page is for people with no session, and
    // asking the server who they are would only produce a 401 in the console.
    useEffect(() => { if (!publicPage) bootstrap() }, []) // eslint-disable-line react-hooks/exhaustive-deps

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

    // Before the auth gate on purpose (issue #262). The registration form links
    // here, and a link from a form you have not submitted yet cannot require the
    // account it is asking you to create.
    if (publicPage) return publicPage

    if (loading) {
        return (
            <div className="loading-page">
                <div className="spinner"></div>
                <span>Loading Tandem...</span>
            </div>
        )
    }

    // The token is still good as far as we know — we just never reached the
    // server. Keep it, say so, and offer the retry (issue #211).
    if (!user && unreachable) {
        return (
            <div className="loading-page">
                <h2>Couldn't reach the server</h2>
                <p style={{ color: 'var(--text-muted)', maxWidth: 420, textAlign: 'center' }}>
                    Tandem couldn't load your session. Check your connection and try again —
                    you are still signed in.
                </p>
                <button className="btn btn-primary" onClick={bootstrap}>Retry</button>
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
