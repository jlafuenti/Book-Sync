import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import App from './App'
import { ThemeProvider } from './ThemeContext'

/**
 * `/account-deletion` must render for someone with no session (issue #146).
 *
 * This is the "web link resource" Google Play requires alongside the in-app
 * path, and its audience is people who have *already uninstalled* — so it has
 * to survive the auth gate in `App`, which otherwise replaces the whole tree
 * with the login form before the router ever sees the path. That gate is the
 * thing this file pins: it is the natural place for the public page to be lost
 * again in a later refactor, silently, with a Play listing pointing at it.
 */

vi.mock('./pages/LoginPage', () => ({ default: () => <div>login-stub</div> }))
vi.mock('./pages/HomePage', () => ({ default: () => <div>home-stub</div> }))
vi.mock('./pages/SystemPage', () => ({ default: () => <div>system-stub</div> }))
vi.mock('./pages/TroubleshootPage', () => ({ default: () => <div>troubleshoot-stub</div> }))
vi.mock('./pages/ImportSourcesPage', () => ({ default: () => <div>import-sources-stub</div> }))
vi.mock('./pages/BookDetailPage', () => ({ default: () => <div>book-detail-stub</div> }))
vi.mock('./pages/TranscriptionPage', () => ({ default: () => <div>transcription-stub</div> }))
vi.mock('./pages/TranscriptionEditorPage', () => ({ default: () => <div>editor-stub</div> }))

const { isLoggedInMock, getMeMock, getUsersMock } = vi.hoisted(() => ({
    isLoggedInMock: vi.fn(),
    getMeMock: vi.fn(),
    getUsersMock: vi.fn().mockResolvedValue([]),
}))

vi.mock('./api', async (importOriginal) => {
    const actual = await importOriginal()
    return {
        ...actual,
        isLoggedIn: isLoggedInMock,
        getMe: getMeMock,
        getUsers: getUsersMock,
        logout: vi.fn().mockResolvedValue(undefined),
    }
})

beforeEach(() => {
    isLoggedInMock.mockReset()
    getMeMock.mockReset()
})

function renderAt(path) {
    render(
        <MemoryRouter initialEntries={[path]}>
            <ThemeProvider>
                <App />
            </ThemeProvider>
        </MemoryRouter>,
    )
}

describe('/account-deletion', () => {
    it('renders logged out, without the login form', async () => {
        isLoggedInMock.mockReturnValue(false)

        renderAt('/account-deletion')

        expect(await screen.findByRole('heading', { name: /delete your tandem account/i }))
            .toBeInTheDocument()
        expect(screen.queryByText('login-stub')).toBeNull()
    })

    it('never asks the server who the visitor is', () => {
        isLoggedInMock.mockReturnValue(false)

        renderAt('/account-deletion')

        expect(getMeMock).not.toHaveBeenCalled()
    })

    it('still renders for someone who is signed in', async () => {
        isLoggedInMock.mockReturnValue(true)
        getMeMock.mockResolvedValue({ username: 'alice', role: 'user' })

        renderAt('/account-deletion')

        expect(await screen.findByRole('heading', { name: /delete your tandem account/i }))
            .toBeInTheDocument()
    })

    it('leaves every other path behind the auth gate', async () => {
        isLoggedInMock.mockReturnValue(false)

        renderAt('/library')

        await waitFor(() => expect(screen.getByText('login-stub')).toBeInTheDocument())
    })
})

describe('/account', () => {
    it('renders the account page for a signed-in user', async () => {
        isLoggedInMock.mockReturnValue(true)
        getMeMock.mockResolvedValue({
            username: 'alice', email: 'alice@example.com', role: 'user',
        })

        renderAt('/account')

        expect(await screen.findByRole('heading', { name: 'Account' })).toBeInTheDocument()
        expect(screen.getByText('alice@example.com')).toBeInTheDocument()
    })

    it('is reachable from the sidebar user block', async () => {
        isLoggedInMock.mockReturnValue(true)
        getMeMock.mockResolvedValue({ username: 'alice', role: 'user' })

        renderAt('/continue')

        await screen.findByText('home-stub')
        const accountLinks = screen.getAllByRole('link')
            .filter(a => a.getAttribute('href') === '/account')
        // Avatar and name/role block both point at it.
        expect(accountLinks.length).toBeGreaterThan(0)
    })

    it('is behind the auth gate like everything else', async () => {
        isLoggedInMock.mockReturnValue(false)

        renderAt('/account')

        await waitFor(() => expect(screen.getByText('login-stub')).toBeInTheDocument())
    })
})
