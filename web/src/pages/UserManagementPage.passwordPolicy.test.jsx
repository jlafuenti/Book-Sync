/**
 * Both admin password fields carry the shared policy (issue #205).
 *
 * These two inputs said `minLength={6}` while the Android sheet demanded 8 and
 * the server accepted 6, so an admin could set a temporary password the phone
 * would then refuse to let the user replace with something of the same length.
 * They now read the bounds from `lib/passwordPolicy`, which is pinned to the
 * server's numbers.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

import { UserManagementSection } from './UserManagementPage'
import { PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH } from '../lib/passwordPolicy'

const { getUsersMock } = vi.hoisted(() => ({ getUsersMock: vi.fn() }))

vi.mock('../api', () => ({
    getUsers: getUsersMock,
    createUser: vi.fn(),
    updateUser: vi.fn(),
    approveUser: vi.fn(),
    resetUserPassword: vi.fn(),
    deleteUser: vi.fn(),
    getAuditLog: vi.fn().mockResolvedValue({ entries: [], total: 0 }),
}))

vi.mock('../contexts/AuthContext', () => ({
    useAuth: () => ({ user: { id: 1, username: 'admin', role: 'superadmin' } }),
}))

beforeEach(() => {
    vi.clearAllMocks()
    getUsersMock.mockResolvedValue([
        { id: 2, username: 'reader', email: 'reader@example.com', role: 'user', is_active: true },
    ])
})

function expectPolicyBounds(input) {
    expect(input.getAttribute('minLength')).toBe(String(PASSWORD_MIN_LENGTH))
    expect(input.getAttribute('maxLength')).toBe(String(PASSWORD_MAX_LENGTH))
}

describe('UserManagementSection password fields', () => {
    it('bounds the create-user temporary password by the shared policy', async () => {
        render(<UserManagementSection />)
        await waitFor(() => expect(getUsersMock).toHaveBeenCalled())

        fireEvent.click(screen.getByText('+ Create User'))

        const field = document.querySelector('input[autocomplete="new-password"]')
        expectPolicyBounds(field)
    })

    it('bounds the admin reset password by the shared policy', async () => {
        render(<UserManagementSection />)
        await waitFor(() => expect(getUsersMock).toHaveBeenCalled())

        fireEvent.click(await screen.findByText('Reset PW'))

        const field = document.querySelector('input[autocomplete="new-password"]')
        expectPolicyBounds(field)
    })
})
