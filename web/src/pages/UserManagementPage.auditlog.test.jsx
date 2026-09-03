/**
 * Audit-log tab: the action label/filter map (issue #296).
 *
 * The server gained a `login_locked` action when the per-username login
 * throttle refuses an attempt. Unmapped actions fall back to the raw string
 * and — worse — never appear in the Action filter, so a lockout run is
 * invisible to an admin trying to filter for it.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

import { UserManagementSection } from './UserManagementPage'
import { formatDate } from '../lib/datetime'

const { getUsersMock, getAuditLogMock } = vi.hoisted(() => ({
    getUsersMock: vi.fn(),
    getAuditLogMock: vi.fn(),
}))

vi.mock('../api', () => ({
    getUsers: getUsersMock,
    createUser: vi.fn(),
    updateUser: vi.fn(),
    approveUser: vi.fn(),
    resetUserPassword: vi.fn(),
    deleteUser: vi.fn(),
    getAuditLog: getAuditLogMock,
}))

vi.mock('../contexts/AuthContext', () => ({
    useAuth: () => ({ user: { id: 1, username: 'admin', role: 'superadmin' } }),
}))

async function openAuditTab() {
    render(<UserManagementSection />)
    fireEvent.click(screen.getByRole('button', { name: 'Audit Log' }))
    await waitFor(() => expect(getAuditLogMock).toHaveBeenCalled())
}

describe('UserManagementSection audit log', () => {
    beforeEach(() => {
        vi.clearAllMocks()
        getUsersMock.mockResolvedValue([])
        getAuditLogMock.mockResolvedValue({
            entries: [{
                id: 1,
                username: null,
                action: 'login_locked',
                target_username: null,
                details: "Login temporarily locked for username 'alice' after repeated failures",
                ip_address: '10.0.0.9',
                created_at: '2026-08-24T12:00:00Z',
            }],
            total: 1,
        })
    })

    it('labels a login_locked row instead of showing the raw action', async () => {
        await openAuditTab()
        expect(await screen.findByText('Login Locked')).toBeTruthy()
        expect(screen.queryByText('login_locked')).toBeNull()
    })

    // Issue #216: `created_at` arrives as naive UTC, so the "Created" column
    // must be read as UTC — not as local time, which lands on the wrong day
    // for part of every day west of UTC.
    it('renders the user "Created" column at the UTC instant the server meant', async () => {
        getUsersMock.mockResolvedValue([{
            id: 2, username: 'alice', email: 'alice@example.com', role: 'user',
            is_active: true, created_at: '2026-08-22T02:30:00',
        }])
        render(<UserManagementSection />)

        expect(await screen.findByText('alice')).toBeTruthy()
        expect(screen.getByText(formatDate('2026-08-22T02:30:00'))).toBeTruthy()
        if (new Date(2026, 7, 22).getTimezoneOffset() > 0) {
            // West of UTC this is the previous day — the old bug showed the 22nd.
            expect(screen.queryByText(new Date('2026-08-22T02:30:00').toLocaleDateString())).toBeNull()
        }
    })

    it('offers login_locked in the Action filter and queries the server for it', async () => {
        await openAuditTab()

        fireEvent.click(screen.getByRole('button', { name: /^Action/ }))
        const option = screen.getByRole('button', { name: 'Login Locked' })
        fireEvent.click(option)

        await waitFor(() =>
            expect(getAuditLogMock).toHaveBeenLastCalledWith(1, 50, 'login_locked')
        )
    })
})
