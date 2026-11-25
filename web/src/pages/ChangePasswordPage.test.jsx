import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import ChangePasswordPage from './ChangePasswordPage'
import { PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH, PASSWORD_POLICY_MESSAGE } from '../lib/passwordPolicy'

const { changePasswordMock, getMeMock } = vi.hoisted(() => ({
    changePasswordMock: vi.fn(),
    getMeMock: vi.fn(),
}))

vi.mock('../api', () => ({
    changePassword: changePasswordMock,
    getMe: getMeMock,
}))

describe('ChangePasswordPage', () => {
    beforeEach(() => {
        vi.clearAllMocks()
    })

    // Issue #58: this card was the last web surface still branded "BookSync"
    // while every other screen said "Tandem".
    it('brands the forced-reset card as Tandem', () => {
        render(<ChangePasswordPage onPasswordChanged={vi.fn()} />)

        const heading = screen.getByRole('heading', { level: 1 })
        expect(heading.textContent).toMatch(/Tandem/)
        expect(heading.textContent).not.toMatch(/BookSync/i)
    })

    it('rejects a confirmation that does not match without calling the API', async () => {
        render(<ChangePasswordPage onPasswordChanged={vi.fn()} />)

        fireEvent.change(screen.getByLabelText(/^current password$/i), { target: { value: 'old-pw' } })
        fireEvent.change(screen.getByLabelText(/^new password$/i), { target: { value: 'new-password' } })
        fireEvent.change(screen.getByLabelText(/^confirm new password$/i), { target: { value: 'mismatch' } })
        fireEvent.click(screen.getByRole('button', { name: /set new password/i }))

        expect(await screen.findByText(/do not match/i)).toBeTruthy()
        expect(changePasswordMock).not.toHaveBeenCalled()
    })

    // Issue #962. The server bumps token_version and revokes every session on
    // success, so this browser's tokens are dead too. The page used to call
    // getMe() with them and hand the (null) result back, which dropped the user
    // on the login screen with no word of why. It now reports the change and
    // lets App say so on the login screen.
    it('reports the change without asking the server who is signed in', async () => {
        changePasswordMock.mockResolvedValue({})
        const onPasswordChanged = vi.fn()

        render(<ChangePasswordPage onPasswordChanged={onPasswordChanged} />)

        fireEvent.change(screen.getByLabelText(/^current password$/i), { target: { value: 'old-pw' } })
        fireEvent.change(screen.getByLabelText(/^new password$/i), { target: { value: 'new-password' } })
        fireEvent.change(screen.getByLabelText(/^confirm new password$/i), { target: { value: 'new-password' } })
        fireEvent.click(screen.getByRole('button', { name: /set new password/i }))

        await waitFor(() => expect(onPasswordChanged).toHaveBeenCalledTimes(1))
        expect(changePasswordMock).toHaveBeenCalledWith('old-pw', 'new-password')
        expect(getMeMock).not.toHaveBeenCalled()
    })

    it('shows the server refusal and stays on the screen', async () => {
        changePasswordMock.mockRejectedValue(new Error('Current password is incorrect'))
        const onPasswordChanged = vi.fn()

        render(<ChangePasswordPage onPasswordChanged={onPasswordChanged} />)

        fireEvent.change(screen.getByLabelText(/^current password$/i), { target: { value: 'wrong' } })
        fireEvent.change(screen.getByLabelText(/^new password$/i), { target: { value: 'new-password' } })
        fireEvent.change(screen.getByLabelText(/^confirm new password$/i), { target: { value: 'new-password' } })
        fireEvent.click(screen.getByRole('button', { name: /set new password/i }))

        expect(await screen.findByText(/Current password is incorrect/)).toBeTruthy()
        expect(onPasswordChanged).not.toHaveBeenCalled()
    })

    // Issue #205: the form used to say "at least 6 characters" while the API
    // enforced 6 and the Android sheet 8. All three now share 8..128.
    it('rejects a password one character under the floor without calling the API', async () => {
        render(<ChangePasswordPage onPasswordChanged={vi.fn()} />)

        const short = 'a'.repeat(PASSWORD_MIN_LENGTH - 1)
        fireEvent.change(screen.getByLabelText(/^current password$/i), { target: { value: 'old-pw' } })
        fireEvent.change(screen.getByLabelText(/^new password$/i), { target: { value: short } })
        fireEvent.change(screen.getByLabelText(/^confirm new password$/i), { target: { value: short } })
        fireEvent.click(screen.getByRole('button', { name: /set new password/i }))

        await waitFor(() => expect(document.querySelector('.alert-error').textContent)
            .toContain(PASSWORD_POLICY_MESSAGE))
        expect(changePasswordMock).not.toHaveBeenCalled()
    })

    it('accepts a password exactly at the floor', async () => {
        changePasswordMock.mockResolvedValue({})
        render(<ChangePasswordPage onPasswordChanged={vi.fn()} />)

        const ok = 'a'.repeat(PASSWORD_MIN_LENGTH)
        fireEvent.change(screen.getByLabelText(/^current password$/i), { target: { value: 'old-pw' } })
        fireEvent.change(screen.getByLabelText(/^new password$/i), { target: { value: ok } })
        fireEvent.change(screen.getByLabelText(/^confirm new password$/i), { target: { value: ok } })
        fireEvent.click(screen.getByRole('button', { name: /set new password/i }))

        await waitFor(() => expect(changePasswordMock).toHaveBeenCalledWith('old-pw', ok))
    })

    it('rejects a password one character over the ceiling without calling the API', async () => {
        render(<ChangePasswordPage onPasswordChanged={vi.fn()} />)

        const long = 'a'.repeat(PASSWORD_MAX_LENGTH + 1)
        fireEvent.change(screen.getByLabelText(/^current password$/i), { target: { value: 'old-pw' } })
        fireEvent.change(screen.getByLabelText(/^new password$/i), { target: { value: long } })
        fireEvent.change(screen.getByLabelText(/^confirm new password$/i), { target: { value: long } })
        fireEvent.click(screen.getByRole('button', { name: /set new password/i }))

        await waitFor(() => expect(document.querySelector('.alert-error').textContent)
            .toContain(PASSWORD_POLICY_MESSAGE))
        expect(changePasswordMock).not.toHaveBeenCalled()
    })
})
