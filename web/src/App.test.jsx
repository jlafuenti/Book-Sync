import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { AppShell } from './App'
import { ThemeProvider } from './ThemeContext'
import { AudioPlayerProvider } from './contexts/AudioPlayerContext'

// HomePage (the default "/continue" route) pulls in its own api calls and
// player wiring that aren't relevant here — stub it so the test only
// exercises the sidebar/logout chrome that AppShell itself owns.
vi.mock('./pages/HomePage', () => ({ default: () => <div>home-stub</div> }))

const { logoutMock } = vi.hoisted(() => ({ logoutMock: vi.fn().mockResolvedValue(undefined) }))
vi.mock('./api', async (importOriginal) => {
    const actual = await importOriginal()
    return { ...actual, logout: logoutMock }
})

function renderShell(user) {
    const setUser = vi.fn()
    render(
        <MemoryRouter initialEntries={['/continue']}>
            <ThemeProvider>
                <AudioPlayerProvider>
                    <AppShell user={user} setUser={setUser} />
                </AudioPlayerProvider>
            </ThemeProvider>
        </MemoryRouter>,
    )
    return { setUser }
}

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
})
