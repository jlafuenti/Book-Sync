import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import SystemPage from './SystemPage'

/**
 * Issue #282: with ALLOW_PUBLIC_REGISTRATION on, a stranger's account request
 * lands as an inactive user and nothing says so. User Management is further
 * down this very page, so the status dashboard is where an admin should see
 * the count — next to the queue counts they already scan.
 *
 * The read is admin-only (GET /api/users/?filter=pending), and a failure must
 * not take the dashboard with it.
 */

const {
    diskMock, ebooksMock, audiobooksMock, pairsMock, queueMock,
    unsupportedMock, settingsMock, calibreMock, getUsersMock, authRef,
} = vi.hoisted(() => ({
    diskMock: vi.fn(),
    ebooksMock: vi.fn(),
    audiobooksMock: vi.fn(),
    pairsMock: vi.fn(),
    queueMock: vi.fn(),
    unsupportedMock: vi.fn(),
    settingsMock: vi.fn(),
    calibreMock: vi.fn(),
    getUsersMock: vi.fn(),
    authRef: { role: 'admin' },
}))

vi.mock('../api', async (importOriginal) => {
    const actual = await importOriginal()
    return {
        ...actual,
        getDiskUsage: diskMock,
        getEbooks: ebooksMock,
        getAudiobooks: audiobooksMock,
        getPairs: pairsMock,
        getTranscriptionQueue: queueMock,
        getUnsupportedFiles: unsupportedMock,
        getSettings: settingsMock,
        getCalibreStatus: calibreMock,
        getUsers: getUsersMock,
    }
})

const ROLE_HIERARCHY = { superadmin: 4, admin: 3, editor: 2, user: 1 }
vi.mock('../contexts/AuthContext', () => ({
    useAuth: () => ({
        hasMinRole: (min) => (ROLE_HIERARCHY[authRef.role] || 0) >= (ROLE_HIERARCHY[min] || 0),
    }),
}))

// UserManagementSection renders further down the same page and fires its own
// admin reads; the tile is what is under test.
vi.mock('./UserManagementPage', () => ({
    UserManagementSection: () => <div>users-stub</div>,
}))

const pending = (n) => Array.from({ length: n }, (_, i) => ({ id: i, username: `u${i}`, is_active: false }))

beforeEach(() => {
    authRef.role = 'admin'
    diskMock.mockReset().mockResolvedValue({ disks: [] })
    ebooksMock.mockReset().mockResolvedValue([])
    audiobooksMock.mockReset().mockResolvedValue([])
    pairsMock.mockReset().mockResolvedValue([])
    queueMock.mockReset().mockResolvedValue([])
    unsupportedMock.mockReset().mockResolvedValue([])
    settingsMock.mockReset().mockResolvedValue({})
    calibreMock.mockReset().mockResolvedValue({ available: false })
    getUsersMock.mockReset().mockResolvedValue([])
})

const renderPage = (tab = 'status') =>
    render(<MemoryRouter><SystemPage tab={tab} /></MemoryRouter>)

describe('pending user requests tile (issue #282)', () => {
    it('shows the count on the status dashboard', async () => {
        getUsersMock.mockResolvedValue(pending(2))
        renderPage()

        const tile = await screen.findByText('Pending User Requests')
        expect(tile.parentElement).toHaveTextContent('2')
        expect(getUsersMock).toHaveBeenCalledWith('pending')
    })

    it('stays out of the way when there are none', async () => {
        renderPage()

        await waitFor(() => expect(getUsersMock).toHaveBeenCalled())
        expect(screen.queryByText('Pending User Requests')).toBeNull()
    })

    it('does not ask from an editor session, which would only 403', async () => {
        authRef.role = 'editor'
        renderPage()

        await new Promise(r => setTimeout(r, 0))
        expect(getUsersMock).not.toHaveBeenCalled()
    })

    it('leaves the dashboard intact when the count cannot be read', async () => {
        getUsersMock.mockRejectedValue(new Error('boom'))
        renderPage()

        expect(await screen.findByText('Total Books')).toBeInTheDocument()
        expect(screen.queryByText('Pending User Requests')).toBeNull()
    })
})
