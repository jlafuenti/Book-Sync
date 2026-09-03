import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
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

const { logoutMock } = vi.hoisted(() => ({ logoutMock: vi.fn().mockResolvedValue(undefined) }))
vi.mock('./api', async (importOriginal) => {
    const actual = await importOriginal()
    return { ...actual, logout: logoutMock }
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
