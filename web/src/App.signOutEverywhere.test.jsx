import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('./pages/HomePage', () => ({ default: () => <div>home-stub</div> }))

const { logoutAllMock, getUsersMock } = vi.hoisted(() => ({
    logoutAllMock: vi.fn().mockResolvedValue(undefined),
    getUsersMock: vi.fn().mockResolvedValue([]),
}))
vi.mock('./api', async (importOriginal) => {
    const actual = await importOriginal()
    return { ...actual, logoutAll: logoutAllMock, getUsers: getUsersMock }
})

import { AppShell } from './App'
import { ThemeProvider } from './ThemeContext'
import { AudioPlayerProvider } from './contexts/AudioPlayerContext'
import { AuthProvider } from './contexts/AuthContext'

const USER = { username: 'alice', role: 'admin' }

function renderShell() {
    const setUser = vi.fn()
    render(
        <MemoryRouter initialEntries={['/continue']}>
            <ThemeProvider>
                <AuthProvider user={USER}>
                    <AudioPlayerProvider>
                        <AppShell user={USER} setUser={setUser} />
                    </AudioPlayerProvider>
                </AuthProvider>
            </ThemeProvider>
        </MemoryRouter>,
    )
    return { setUser }
}

describe('AppShell — sign out everywhere (issue #250)', () => {
    let confirmSpy

    beforeEach(() => {
        logoutAllMock.mockReset().mockResolvedValue(undefined)
        confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    })

    afterEach(() => {
        confirmSpy.mockRestore()
        vi.clearAllMocks()
    })

    it('offers the account-wide sign-out alongside the per-device Logout', () => {
        renderShell()

        // Both, and distinctly: #250 made the icon button end this browser only.
        expect(screen.getByTitle('Logout')).toBeTruthy()
        expect(screen.getByTestId('sign-out-everywhere')).toBeTruthy()
    })

    it('drops the user once the account-wide revoke has been sent', async () => {
        const { setUser } = renderShell()

        fireEvent.click(screen.getByTestId('sign-out-everywhere'))

        await waitFor(() => expect(setUser).toHaveBeenCalledWith(null))
        expect(logoutAllMock).toHaveBeenCalledTimes(1)
    })
})
