import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import AccountPage from './AccountPage'

/**
 * Self-service account deletion on the web (issue #146).
 *
 * Google Play requires an in-app deletion path *and* a public web link for any
 * app that can create an account; the web app is the other half of the same
 * account, so the control belongs here too.
 *
 * The behaviour worth pinning is the gate, not the styling: the API must not be
 * called until both the password and the literal word are supplied, a refusal
 * must show the server's own sentence (403 "password incorrect" and 409 "last
 * superadmin" need different actions from the user), and only a success may end
 * the session.
 */

const { deleteAccountMock } = vi.hoisted(() => ({ deleteAccountMock: vi.fn() }))

vi.mock('../api', () => ({ deleteAccount: deleteAccountMock }))

beforeEach(() => {
    deleteAccountMock.mockReset()
})

const user = { username: 'alice', email: 'alice@example.com', role: 'user' }

function renderPage(props = {}) {
    return render(
        <MemoryRouter>
            <AccountPage user={user} onAccountDeleted={vi.fn()} {...props} />
        </MemoryRouter>,
    )
}

function openDeleteForm() {
    fireEvent.click(screen.getByRole('button', { name: /delete account/i }))
}

function fill({ password = 'hunter2', confirm = 'DELETE' } = {}) {
    fireEvent.change(screen.getByLabelText(/current password/i), {
        target: { value: password },
    })
    fireEvent.change(screen.getByLabelText(/type delete/i), {
        target: { value: confirm },
    })
}

describe('AccountPage', () => {
    it('shows who is signed in', () => {
        renderPage()
        expect(screen.getByText('alice')).toBeInTheDocument()
        expect(screen.getByText('alice@example.com')).toBeInTheDocument()
    })

    it('keeps the deletion form closed until it is asked for', () => {
        renderPage()
        expect(screen.queryByLabelText(/current password/i)).toBeNull()
    })
})

describe('AccountPage — delete account', () => {
    it('does not call the API until the password and the word are both given', () => {
        renderPage()
        openDeleteForm()

        const confirmBtn = screen.getByTestId('confirm-delete-account')
        expect(confirmBtn).toBeDisabled()

        fill({ confirm: '' })
        expect(confirmBtn).toBeDisabled()

        fireEvent.click(confirmBtn)
        expect(deleteAccountMock).not.toHaveBeenCalled()
    })

    it('will not accept a near-miss of the confirmation word', () => {
        renderPage()
        openDeleteForm()
        fill({ confirm: 'delete' })

        expect(screen.getByTestId('confirm-delete-account')).toBeDisabled()
        expect(deleteAccountMock).not.toHaveBeenCalled()
    })

    it('sends the password once both fields are right, then ends the session', async () => {
        deleteAccountMock.mockResolvedValue(undefined)
        const onAccountDeleted = vi.fn()
        renderPage({ onAccountDeleted })
        openDeleteForm()
        fill()

        fireEvent.click(screen.getByTestId('confirm-delete-account'))

        await waitFor(() => expect(deleteAccountMock).toHaveBeenCalledWith('hunter2'))
        await waitFor(() => expect(onAccountDeleted).toHaveBeenCalled())
    })

    it('shows the server’s refusal and keeps the session', async () => {
        deleteAccountMock.mockRejectedValue(new Error('Password is incorrect'))
        const onAccountDeleted = vi.fn()
        renderPage({ onAccountDeleted })
        openDeleteForm()
        fill({ password: 'wrong' })

        fireEvent.click(screen.getByTestId('confirm-delete-account'))

        expect(await screen.findByText('Password is incorrect')).toBeInTheDocument()
        expect(onAccountDeleted).not.toHaveBeenCalled()
    })

    it('surfaces the last-superadmin refusal, which nothing in the UI can fix', async () => {
        const detail = 'You are the last active superadmin. Promote another user first.'
        deleteAccountMock.mockRejectedValue(new Error(detail))
        renderPage({ user: { ...user, role: 'superadmin' } })
        openDeleteForm()
        fill()

        fireEvent.click(screen.getByTestId('confirm-delete-account'))

        expect(await screen.findByText(detail)).toBeInTheDocument()
    })

    it('links to the public deletion page, which is what Play asks for', () => {
        renderPage()
        openDeleteForm()
        expect(screen.getByRole('link', { name: /how account deletion works/i }))
            .toHaveAttribute('href', '/account-deletion')
    })
})
