import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

// The whole point of the route is that it renders with no session, so isLoggedIn
// reports none and getMe records any call App makes anyway.
const { isLoggedInMock, getMeMock, getUsersMock } = vi.hoisted(() => ({
    isLoggedInMock: vi.fn().mockReturnValue(false),
    getMeMock: vi.fn().mockResolvedValue(null),
    getUsersMock: vi.fn().mockResolvedValue([]),
}))

vi.mock('./api', async (importOriginal) => {
    const actual = await importOriginal()
    return {
        ...actual,
        isLoggedIn: isLoggedInMock,
        getMe: getMeMock,
        getUsers: getUsersMock,
    }
})

import App from './App'
import { ThemeProvider } from './ThemeContext'

const renderAt = (path) =>
    render(
        <ThemeProvider>
            <MemoryRouter initialEntries={[path]}>
                <App />
            </MemoryRouter>
        </ThemeProvider>,
    )

describe('/terms is public (issue #262)', () => {
    beforeEach(() => {
        localStorage.clear()
        isLoggedInMock.mockReturnValue(false)
        getMeMock.mockResolvedValue(null)
    })

    afterEach(() => {
        vi.clearAllMocks()
    })

    it('renders the terms page with no session instead of the login form', async () => {
        renderAt('/terms')

        expect(await screen.findByText('Terms of use')).toBeTruthy()
        expect(screen.queryByLabelText('Password')).toBeNull()
    })

    it('does not ask the server who the visitor is', async () => {
        renderAt('/terms')

        await screen.findByText('Terms of use')
        expect(getMeMock).not.toHaveBeenCalled()
    })

    it('still shows the login form on every other path', async () => {
        renderAt('/library')

        expect(await screen.findByLabelText('Password')).toBeTruthy()
        expect(screen.queryByText('Terms of use')).toBeNull()
    })
})
