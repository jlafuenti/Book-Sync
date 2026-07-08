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
