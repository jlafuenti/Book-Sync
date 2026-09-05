import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { UserManagementSection } from './UserManagementPage'

// Issue #210. Invites are the whole of `registration_mode = "invite"`, and an
// admin has no other way to issue one — there is no CLI.

const mocks = vi.hoisted(() => ({
    getUsers: vi.fn(),
    createUser: vi.fn(),
    updateUser: vi.fn(),
    approveUser: vi.fn(),
    resetUserPassword: vi.fn(),
    deleteUser: vi.fn(),
    getAuditLog: vi.fn(),
    createInvite: vi.fn(),
    getInvites: vi.fn(),
    revokeInvite: vi.fn(),
}))

vi.mock('../api', () => mocks)
vi.mock('../contexts/AuthContext', () => ({
    useAuth: () => ({ user: { id: 1, username: 'boss', role: 'admin' } }),
}))

beforeEach(() => {
    Object.values(mocks).forEach(m => m.mockReset())
    mocks.getUsers.mockResolvedValue([])
    mocks.getAuditLog.mockResolvedValue({ entries: [], total: 0 })
    mocks.getInvites.mockResolvedValue([])
})

const openInvites = async () => {
    render(<UserManagementSection />)
    fireEvent.click(await screen.findByRole('button', { name: /invites/i }))
    await waitFor(() => expect(mocks.getInvites).toHaveBeenCalled())
}

describe('UserManagementPage — invites', () => {
    it('lists existing invites', async () => {
        mocks.getInvites.mockResolvedValue([
            {
                id: 7,
                status: 'active',
                created_at: '2026-09-01T10:00:00',
                expires_at: '2026-09-08T10:00:00',
                created_by: 'boss',
                used_by: null,
            },
        ])

        await openInvites()

        expect(await screen.findByText('active')).toBeTruthy()
        expect(screen.getByText('boss')).toBeTruthy()
    })

    it('shows the code once, after creating one', async () => {
        mocks.createInvite.mockResolvedValue({ id: 8, code: 'SECRET-CODE', status: 'active' })

        await openInvites()
        fireEvent.click(screen.getByRole('button', { name: /create invite/i }))

        expect(await screen.findByText(/SECRET-CODE/)).toBeTruthy()
        // And the list is reloaded, where the code does not appear.
        await waitFor(() => expect(mocks.getInvites).toHaveBeenCalledTimes(2))
    })

    it('says the code will not be shown again', async () => {
        mocks.createInvite.mockResolvedValue({ id: 8, code: 'SECRET-CODE', status: 'active' })

        await openInvites()
        fireEvent.click(screen.getByRole('button', { name: /create invite/i }))

        expect(await screen.findByText(/not be shown again/i)).toBeTruthy()
    })

    it('revokes an invite and reloads', async () => {
        mocks.getInvites.mockResolvedValue([
            {
                id: 7, status: 'active',
                created_at: '2026-09-01T10:00:00',
                expires_at: '2026-09-08T10:00:00',
                created_by: 'boss', used_by: null,
            },
        ])
        mocks.revokeInvite.mockResolvedValue(undefined)
        vi.spyOn(window, 'confirm').mockReturnValue(true)

        await openInvites()
        fireEvent.click(await screen.findByRole('button', { name: /revoke/i }))

        await waitFor(() => expect(mocks.revokeInvite).toHaveBeenCalledWith(7))
        await waitFor(() => expect(mocks.getInvites).toHaveBeenCalledTimes(2))
    })

    it('surfaces a failure instead of failing silently', async () => {
        mocks.createInvite.mockRejectedValue(new Error('Requires admin role or higher'))

        await openInvites()
        fireEvent.click(screen.getByRole('button', { name: /create invite/i }))

        expect(await screen.findByText(/Requires admin role or higher/)).toBeTruthy()
    })

    it('does not offer a revoke on an invite that is already used', async () => {
        mocks.getInvites.mockResolvedValue([
            {
                id: 7, status: 'used',
                created_at: '2026-09-01T10:00:00',
                expires_at: '2026-09-08T10:00:00',
                created_by: 'boss', used_by: 'alice',
            },
        ])

        await openInvites()

        expect(await screen.findByText('alice')).toBeTruthy()
        expect(screen.queryByRole('button', { name: /revoke/i })).toBeNull()
    })
})
