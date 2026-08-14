import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import ChangePasswordPage from './ChangePasswordPage'

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

        fireEvent.change(screen.getByLabelText('Current Password'), { target: { value: 'old-pw' } })
        fireEvent.change(screen.getByLabelText('New Password'), { target: { value: 'new-password' } })
        fireEvent.change(screen.getByLabelText('Confirm New Password'), { target: { value: 'mismatch' } })
        fireEvent.click(screen.getByRole('button', { name: /set new password/i }))

        expect(await screen.findByText(/do not match/i)).toBeTruthy()
        expect(changePasswordMock).not.toHaveBeenCalled()
    })

    it('hands the refreshed user back to the caller on success', async () => {
        changePasswordMock.mockResolvedValue({})
        getMeMock.mockResolvedValue({ id: 1, username: 'admin', must_reset_password: false })
        const onPasswordChanged = vi.fn()

        render(<ChangePasswordPage onPasswordChanged={onPasswordChanged} />)

        fireEvent.change(screen.getByLabelText('Current Password'), { target: { value: 'old-pw' } })
        fireEvent.change(screen.getByLabelText('New Password'), { target: { value: 'new-password' } })
        fireEvent.change(screen.getByLabelText('Confirm New Password'), { target: { value: 'new-password' } })
        fireEvent.click(screen.getByRole('button', { name: /set new password/i }))

        await waitFor(() => {
            expect(changePasswordMock).toHaveBeenCalledWith('old-pw', 'new-password')
            expect(onPasswordChanged).toHaveBeenCalledWith(
                expect.objectContaining({ username: 'admin' }),
            )
        })
    })
})
