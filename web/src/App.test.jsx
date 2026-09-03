import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { AppShell } from './App'
import { ThemeProvider } from './ThemeContext'
import { AudioPlayerProvider } from './contexts/AudioPlayerContext'
import { AuthProvider } from './contexts/AuthContext'

// HomePage (the default "/continue" route) pulls in its own api calls and
// player wiring that aren't relevant here — stub it so the test only
// exercises the sidebar/logout chrome that AppShell itself owns.
vi.mock('./pages/HomePage', () => ({ default: () => <div>home-stub</div> }))

// The admin-console pages fire their own loads on mount; the role gate is what
// is under test here, not their contents.
// LoginPage renders the real login form and its own api wiring; what matters
// here is only that the app fell back to it (issue #268).
vi.mock('./pages/LoginPage', () => ({ default: () => <div>login-stub</div> }))

vi.mock('./pages/SystemPage', () => ({ default: () => <div>system-stub</div> }))
vi.mock('./pages/TroubleshootPage', () => ({ default: () => <div>troubleshoot-stub</div> }))
vi.mock('./pages/ImportSourcesPage', () => ({ default: () => <div>import-sources-stub</div> }))

const { logoutMock, getUsersMock } = vi.hoisted(() => ({
    logoutMock: vi.fn().mockResolvedValue(undefined),
    // The pending-registration badge (issue #282) reads the admin user list on
    // mount; every AppShell render in this file goes through it.
    getUsersMock: vi.fn().mockResolvedValue([]),
}))
vi.mock('./api', async (importOriginal) => {
    const actual = await importOriginal()
    return { ...actual, logout: logoutMock, getUsers: getUsersMock }
})

// AuthProvider is load-bearing: without it `hasMinRole` comes from the context
// default, which returns false for every role, and every gated route would
// redirect no matter who is signed in. This file used to render without it and
// passed only because AppShell never called hasMinRole (issue #283).
// Reports where the router actually ended up, so a test can tell "rendered at
// this path" apart from "was redirected somewhere that happens to look right".
function LocationProbe() {
    return <div data-testid="pathname">{useLocation().pathname}</div>
}

function renderShell(user, initialEntry = '/continue') {
    const setUser = vi.fn()
    render(
        <MemoryRouter initialEntries={[initialEntry]}>
            <ThemeProvider>
                <AuthProvider user={user}>
                    <AudioPlayerProvider>
                        <AppShell user={user} setUser={setUser} />
                        <LocationProbe />
                    </AudioPlayerProvider>
                </AuthProvider>
            </ThemeProvider>
        </MemoryRouter>,
    )
    return { setUser }
}

// Issue #266: every installed PWA launches at the manifest's start_url. The
// page that once owned that path (ContinuePage) was deleted in PR #140, and
// the only assertion on the string compares the manifest to itself — so if
// HomePage's route were renamed, /continue would fall through to the catch-all
// and installed apps would open somewhere unexpected with CI still green.
// This is the one test that reads the manifest and drives the real router.
describe('PWA start_url', () => {
    const manifest = JSON.parse(readFileSync(
        resolve(dirname(fileURLToPath(import.meta.url)), '../public/manifest.webmanifest'), 'utf-8',
    ))

    it('renders Home at the manifest start_url without redirecting', () => {
        renderShell({ username: 'alice', role: 'user' }, manifest.start_url)

        expect(screen.getByText('home-stub')).toBeInTheDocument()
        expect(screen.getByTestId('pathname')).toHaveTextContent(manifest.start_url)
    })
})

describe('AppShell — logout', () => {
    it('calls the server logout endpoint, then clears the signed-in user', async () => {
        logoutMock.mockClear()
        const { setUser } = renderShell({ username: 'alice', role: 'admin' })

        fireEvent.click(screen.getByTitle('Logout'))

        await waitFor(() => expect(logoutMock).toHaveBeenCalledTimes(1))
        await waitFor(() => expect(setUser).toHaveBeenCalledWith(null))
    })
})


// ---------------------------------------------------------------------------
// Mid-session password-reset gate (issue #209)
//
// App gates on user.must_reset_password, but only from the getMe() it runs once
// at mount. When an admin resets your password while your tab is open, the
// server starts refusing every route with 403 password_reset_required while the
// tab carries on as though nothing happened — every action failing, with no
// explanation and no way to the reset screen short of a reload. api.js announces
// the refusal; this is the half that acts on it.
// ---------------------------------------------------------------------------

vi.mock('./pages/ChangePasswordPage', () => ({
    default: () => <div>change-password-stub</div>,
}))

describe('App — password_reset_required arriving mid-session', () => {
    async function renderApp(user) {
        const api = await import('./api')
        vi.spyOn(api, 'isLoggedIn').mockReturnValue(true)
        vi.spyOn(api, 'getMe').mockResolvedValue(user)

        const AppDefault = (await import('./App')).default
        render(
            <MemoryRouter initialEntries={['/continue']}>
                <ThemeProvider>
                    <AppDefault />
                </ThemeProvider>
            </MemoryRouter>,
        )
        await screen.findByText('home-stub')
    }

    it('shows the change-password screen when the event fires', async () => {
        await renderApp({ username: 'alice', role: 'admin', must_reset_password: false })

        window.dispatchEvent(new CustomEvent('tandem:password-reset-required'))

        expect(await screen.findByText('change-password-stub')).toBeTruthy()
    })

    it('leaves the app alone until the event fires', async () => {
        await renderApp({ username: 'alice', role: 'admin', must_reset_password: false })

        expect(screen.queryByText('change-password-stub')).toBeNull()
        expect(screen.getByText('home-stub')).toBeTruthy()
    })

    // Issue #268. Same shape as the gate above, for the case where the session
    // is over rather than merely restricted: api.js dispatches this instead of
    // reloading the page, so an open reader keeps its pending save.
    it('drops to the login screen when the session cannot be refreshed', async () => {
        await renderApp({ username: 'alice', role: 'admin', must_reset_password: false })

        window.dispatchEvent(new CustomEvent('tandem:unauthorized'))

        expect(await screen.findByText('login-stub')).toBeTruthy()
        expect(screen.queryByText('home-stub')).toBeNull()
    })
})


// ---------------------------------------------------------------------------
// Issue #283: the /system routes were plain <Route> entries, so any signed-in
// user could type the URL, get the Admin Console shell, and have it fire the
// admin-flavoured reads. No privilege was gained -- the mutating endpoints were
// always gated -- but disk capacity, backup health and internal hostnames were
// visible to anyone with an account.
//
// The server checks added alongside these are the real barrier; this is the
// defence-in-depth layer everyone already assumed was here.
//
// Troubleshoot and Unsupported sit at *editor*, not admin: both are library
// maintenance, and TroubleshootPage's own fix controls are already editor-gated,
// so gating its route at admin would have shut out the role it was built for.
// ---------------------------------------------------------------------------

describe('AppShell — role-gated routes', () => {
    const cases = [
        ['/system', 'system-stub', ['admin', 'superadmin'], ['user', 'editor']],
        ['/system/status', 'system-stub', ['admin'], ['user', 'editor']],
        ['/system/import-sources', 'import-sources-stub', ['admin'], ['user', 'editor']],
        ['/system/unsupported', 'system-stub', ['editor', 'admin'], ['user']],
        ['/system/troubleshoot', 'troubleshoot-stub', ['editor', 'admin'], ['user']],
    ]

    for (const [path, stub, allowed, denied] of cases) {
        for (const role of allowed) {
            it(`renders ${path} for ${role}`, async () => {
                renderShell({ username: 'x', role }, path)
                expect(await screen.findByText(stub)).toBeTruthy()
            })
        }
        for (const role of denied) {
            it(`redirects ${path} away from ${role}`, async () => {
                renderShell({ username: 'x', role }, path)
                expect(await screen.findByText('home-stub')).toBeTruthy()
                expect(screen.queryByText(stub)).toBeNull()
            })
        }
    }

    it('leaves /transcription reachable by everyone', async () => {
        // Deliberate: the queue view is useful to a plain reader, so the page
        // stays readable and hides its write controls instead (issue #312).
        renderShell({ username: 'x', role: 'user' }, '/transcription')
        expect(screen.queryByText('home-stub')).toBeNull()
    })
})

describe('AppShell — the System nav entry follows the gate', () => {
    it('is hidden from a plain user', () => {
        renderShell({ username: 'x', role: 'user' })
        expect(screen.queryByTitle('System')).toBeNull()
    })

    it('is shown to an editor, pointing at a page they can open', () => {
        renderShell({ username: 'x', role: 'editor' })
        expect(screen.getByTitle('System').getAttribute('href')).toBe('/system/troubleshoot')
    })

    it('is shown to an admin, pointing at status', () => {
        renderShell({ username: 'x', role: 'admin' })
        expect(screen.getByTitle('System').getAttribute('href')).toBe('/system/status')
    })
})


// ---------------------------------------------------------------------------
// Issue #282: with ALLOW_PUBLIC_REGISTRATION on, strangers create pending
// accounts and an admin only notices by opening System -> Users. The count is
// one admin-only query away, so it rides on the nav entry that leads there.
//
// Admin-only because GET /api/users/?filter=pending is: firing it from an
// editor's session would buy a 403 and nothing else.
// ---------------------------------------------------------------------------

describe('AppShell — pending-registration badge (issue #282)', () => {
    const pending = (n) => Array.from({ length: n }, (_, i) => ({ id: i, username: `u${i}`, is_active: false }))

    beforeEach(() => {
        getUsersMock.mockReset().mockResolvedValue([])
    })

    it('shows the count on the System nav entry for an admin', async () => {
        getUsersMock.mockResolvedValue(pending(2))
        renderShell({ username: 'x', role: 'admin' })

        await waitFor(() => expect(screen.getByTitle('System')).toHaveTextContent('2'))
        expect(getUsersMock).toHaveBeenCalledWith('pending')
    })

    it('shows nothing when there are no pending requests', async () => {
        getUsersMock.mockResolvedValue([])
        renderShell({ username: 'x', role: 'admin' })

        await waitFor(() => expect(getUsersMock).toHaveBeenCalled())
        expect(screen.queryByTestId('pending-users-badge')).toBeNull()
    })

    it('does not ask at all from an editor session, which would only 403', async () => {
        renderShell({ username: 'x', role: 'editor' })

        await new Promise(r => setTimeout(r, 0))
        expect(getUsersMock).not.toHaveBeenCalled()
        expect(screen.getByTitle('System')).not.toHaveTextContent('2')
    })

    it('stays quiet when the count cannot be read', async () => {
        getUsersMock.mockRejectedValue(new Error('boom'))
        renderShell({ username: 'x', role: 'admin' })

        await waitFor(() => expect(getUsersMock).toHaveBeenCalled())
        expect(screen.queryByTestId('pending-users-badge')).toBeNull()
        expect(screen.getByTitle('System')).toBeInTheDocument()
    })
})


// ---------------------------------------------------------------------------
// Issue #211: the boot sequence and the auth guard.
//
// App's mount-time isLoggedIn() -> getMe() decides which of three screens every
// user sees, and none of it was covered. Two of the branches are the ones that
// matter most on an internet-facing deployment: must_reset_password (letting a
// bootstrap-password account past it is a real security hole) and the rejected
// getMe(), which used to swallow the failure and drop a signed-in user on the
// login form with no explanation — indistinguishable from "your session ended".
// ---------------------------------------------------------------------------

describe('App — boot and the auth guard', () => {
    async function bootApp({ loggedIn = true, me } = {}) {
        const api = await import('./api')
        vi.spyOn(api, 'isLoggedIn').mockReturnValue(loggedIn)
        const getMe = vi.spyOn(api, 'getMe')
        if (me instanceof Error) getMe.mockRejectedValue(me)
        else getMe.mockResolvedValue(me ?? null)

        const AppDefault = (await import('./App')).default
        // act() around the render so the bootstrap promise settles inside it —
        // otherwise every one of these tests prints an act() warning.
        await act(async () => {
            render(
                <MemoryRouter initialEntries={['/continue']}>
                    <ThemeProvider>
                        <AppDefault />
                    </ThemeProvider>
                </MemoryRouter>,
            )
        })
    }

    it('renders the login form when there is no token, without calling getMe', async () => {
        const api = await import('./api')
        await bootApp({ loggedIn: false })

        expect(await screen.findByText('login-stub')).toBeTruthy()
        expect(api.getMe).not.toHaveBeenCalled()
    })

    it('renders the app for a normal signed-in user', async () => {
        await bootApp({ me: { username: 'alice', role: 'admin', must_reset_password: false } })

        expect(await screen.findByText('home-stub')).toBeTruthy()
    })

    it('applies the profile theme at boot', async () => {
        localStorage.setItem('tandem_theme', 'blueprint')
        await bootApp({ me: { username: 'alice', role: 'admin', theme: 'ember' } })

        await screen.findByText('home-stub')
        expect(localStorage.getItem('tandem_theme')).toBe('ember')
    })

    it('gates a must_reset_password account on the change-password screen', async () => {
        await bootApp({ me: { username: 'alice', role: 'admin', must_reset_password: true } })

        expect(await screen.findByText('change-password-stub')).toBeTruthy()
        // The shell must not be behind it: no nav, no routed page.
        expect(screen.queryByText('home-stub')).toBeNull()
        expect(screen.queryByTitle('Logout')).toBeNull()
    })

    it('drops to the login form when the session is rejected (401 -> getMe null)', async () => {
        await bootApp({ me: null })

        expect(await screen.findByText('login-stub')).toBeTruthy()
    })

    it('offers a retry instead of the login form when the server is unreachable', async () => {
        // A rejected getMe() is a network/proxy failure, not an expired session.
        // Showing the bare login form here tells the user they were signed out
        // and invites them to retype a password that will not get through either.
        await bootApp({ me: new TypeError('Failed to fetch') })

        expect(await screen.findByText(/Couldn't reach the server/i)).toBeTruthy()
        expect(screen.queryByText('login-stub')).toBeNull()
    })

    it('retries the bootstrap when the retry control is clicked', async () => {
        const api = await import('./api')
        await bootApp({ me: new TypeError('Failed to fetch') })
        await screen.findByText(/Couldn't reach the server/i)

        api.getMe.mockResolvedValue({ username: 'alice', role: 'admin' })
        fireEvent.click(screen.getByRole('button', { name: /retry/i }))

        expect(await screen.findByText('home-stub')).toBeTruthy()
        expect(api.getMe).toHaveBeenCalledTimes(2)
    })
})
