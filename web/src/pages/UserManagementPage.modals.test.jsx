/**
 * The two admin modals are the shared Modal primitive now (issue #279).
 *
 * Both were hand-rolled overlays with a stopPropagation'd child: no dialog
 * role, no focus trap, no Escape. They kept their backdrop-click close.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

import { UserManagementSection } from './UserManagementPage'

const { getUsersMock, createUserMock, resetUserPasswordMock } = vi.hoisted(() => ({
    getUsersMock: vi.fn(),
    createUserMock: vi.fn(),
    resetUserPasswordMock: vi.fn(),
}))

vi.mock('../api', () => ({
    getUsers: getUsersMock,
    createUser: createUserMock,
    updateUser: vi.fn(),
    approveUser: vi.fn(),
    resetUserPassword: resetUserPasswordMock,
    deleteUser: vi.fn(),
    getAuditLog: vi.fn(async () => ({ entries: [], total: 0 })),
}))

vi.mock('../contexts/AuthContext', () => ({
    useAuth: () => ({ user: { id: 1, username: 'admin', role: 'superadmin' } }),
}))

const OTHER_USER = {
    id: 2, username: 'reader', email: 'reader@example.invalid', role: 'user',
    is_active: true, is_approved: true, created_at: '2026-01-01T00:00:00',
}

beforeEach(() => {
    vi.clearAllMocks()
    getUsersMock.mockResolvedValue([OTHER_USER])
    createUserMock.mockResolvedValue({})
    resetUserPasswordMock.mockResolvedValue({})
})

describe('Create User modal', () => {
    it('is a labelled dialog, focuses inside, and Escape returns focus to the trigger', async () => {
        render(<UserManagementSection />)
        const trigger = await screen.findByRole('button', { name: '+ Create User' })

        trigger.focus()
        fireEvent.click(trigger)

        const dialog = screen.getByRole('dialog')
        expect(dialog).toHaveAttribute('aria-modal', 'true')
        expect(dialog).toHaveAccessibleName('Create User')
        expect(dialog.contains(document.activeElement)).toBe(true)

        fireEvent.keyDown(document, { key: 'Escape' })
        expect(screen.queryByRole('dialog')).toBeNull()
        expect(document.activeElement).toBe(trigger)
    })

    it('still closes on a backdrop click', async () => {
        const { container } = render(<UserManagementSection />)
        fireEvent.click(await screen.findByRole('button', { name: '+ Create User' }))
        expect(screen.getByRole('dialog')).toBeInTheDocument()

        fireEvent.click(container.querySelector('.modal-overlay'))
        expect(screen.queryByRole('dialog')).toBeNull()
    })
})

describe('Reset Password modal', () => {
    it('is labelled with the user it targets and still submits', async () => {
        const { container } = render(<UserManagementSection />)
        fireEvent.click(await screen.findByRole('button', { name: 'Reset PW' }))

        const dialog = screen.getByRole('dialog')
        expect(dialog).toHaveAccessibleName('Reset Password — reader')

        fireEvent.change(container.querySelector('input[type="password"]'), { target: { value: 'hunter22' } })
        fireEvent.click(screen.getByRole('button', { name: 'Reset Password' }))

        await waitFor(() => expect(resetUserPasswordMock).toHaveBeenCalledWith(2, 'hunter22'))
        await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    })
})
