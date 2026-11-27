import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import AccountPage from './AccountPage'
import { TourScreens, TourRegistryContext } from '../tour/anchors'
import { TourAnchorRegistry } from '../tour/TourAnchorRegistry'
import { PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH, PASSWORD_POLICY_MESSAGE } from '../lib/passwordPolicy'

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

const { deleteAccountMock, changePasswordMock } = vi.hoisted(() => ({
    deleteAccountMock: vi.fn(),
    changePasswordMock: vi.fn(),
}))

vi.mock('../api', () => ({
    deleteAccount: deleteAccountMock,
    changePassword: changePasswordMock,
}))

beforeEach(() => {
    deleteAccountMock.mockReset()
    changePasswordMock.mockReset()
    vi.restoreAllMocks()
})

const user = { username: 'alice', email: 'alice@example.com', role: 'user' }

function renderPage(props = {}) {
    return render(
        <MemoryRouter>
            <AccountPage
                user={user}
                onAccountDeleted={vi.fn()}
                onPasswordChanged={vi.fn()}
                {...props}
            />
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

// Issue #714: the project's community Discord, reachable from Account on both
// the web and the Android app. One constant (lib/community.js) holds the invite.
describe('AccountPage — delete account cancel', () => {
    it('closes the form and forgets what was typed', () => {
        renderPage()
        openDeleteForm()
        fill()
        fireEvent.click(screen.getByRole('button', { name: /^cancel$/i }))

        expect(screen.queryByTestId('confirm-delete-account')).toBeNull()
        openDeleteForm()
        expect(screen.getByLabelText(/current password/i)).toHaveValue('')
        expect(screen.getByLabelText(/type delete to confirm/i)).toHaveValue('')
        expect(deleteAccountMock).not.toHaveBeenCalled()
    })
})

describe('AccountPage — community link (issue #714)', () => {
    it('links to the community Discord in a new tab, without leaking the opener', () => {
        renderPage()

        const link = screen.getByRole('link', { name: /join the community on discord/i })
        expect(link.getAttribute('href')).toBe('https://discord.gg/nt9xFKFus')
        expect(link.getAttribute('target')).toBe('_blank')
        expect(link.getAttribute('rel')).toContain('noopener')
        expect(link.getAttribute('rel')).toContain('noreferrer')
    })
})

// Issue #598: the guided walkthrough is offered once (TourProvider) but must
// stay reachable afterward — this is that way back in.
describe('AccountPage — Help card / replay the walkthrough (issue #598)', () => {
    it('shows a "Replay the walkthrough" row, tagged as the AccountReplayTour anchor', () => {
        renderPage()
        expect(screen.getByText('Help')).toBeInTheDocument()
        expect(screen.getByText('Replay the walkthrough')).toBeInTheDocument()
        expect(screen.getByText('About five minutes, on your own library.')).toBeInTheDocument()
        const anchorNode = screen.getByText('Replay the walkthrough').closest('[data-tour]')
        expect(anchorNode).toHaveAttribute('data-tour', 'AccountReplayTour')
    })

    it('calls useTour().replay() when clicked, and does not crash outside a TourProvider', () => {
        renderPage()
        // Rendered without a <TourProvider> (like every other page test in this
        // file); useTour() falls back to a no-op, so the click must not throw.
        expect(() => fireEvent.click(screen.getByRole('button', { name: 'Replay' }))).not.toThrow()
    })

    // Issue #598 Track B: nothing on this page loads asynchronously, so it
    // reports Account settled the moment it mounts, unlike every data-backed
    // page which starts loading.
    it('reports Account settled immediately on mount', () => {
        const registry = new TourAnchorRegistry()
        render(
            <TourRegistryContext.Provider value={registry}>
                <MemoryRouter><AccountPage user={user} onAccountDeleted={vi.fn()} /></MemoryRouter>
            </TourRegistryContext.Provider>,
        )
        expect(registry.screenState(TourScreens.Account)).toBe('settled')
    })
})

// Issue #962: a signed-in web user could not change their own password; only
// the forced-reset screen had a form. Same endpoint, same rules as Android's
// Account -> Change password.
describe('AccountPage — change password (issue #962)', () => {
    function openChangeForm() {
        fireEvent.click(screen.getByRole('button', { name: /^change password$/i }))
    }

    function fillChange({ current = 'old-password', next = 'new-password', confirm = next } = {}) {
        fireEvent.change(screen.getByLabelText(/^current password$/i), { target: { value: current } })
        fireEvent.change(screen.getByLabelText(/^new password$/i), { target: { value: next } })
        fireEvent.change(screen.getByLabelText(/^confirm new password$/i), { target: { value: confirm } })
    }

    function submit() {
        fireEvent.click(screen.getByRole('button', { name: /^change password and sign out$/i }))
    }

    it('keeps the form closed until it is asked for', () => {
        renderPage()
        expect(screen.queryByLabelText(/^new password$/i)).toBeNull()
        openChangeForm()
        expect(screen.getByLabelText(/^new password$/i)).toBeInTheDocument()
    })

    it('says up front that it signs out every device', () => {
        renderPage()
        openChangeForm()
        expect(screen.getByText(/signs you out on every device/i)).toBeInTheDocument()
    })

    it('rejects a confirmation that does not match without calling the API', async () => {
        renderPage()
        openChangeForm()
        fillChange({ confirm: 'something-else' })
        submit()

        expect(await screen.findByText(/do not match/i)).toBeInTheDocument()
        expect(changePasswordMock).not.toHaveBeenCalled()
    })

    it('rejects a password under the floor without calling the API', async () => {
        renderPage()
        openChangeForm()
        const short = 'a'.repeat(PASSWORD_MIN_LENGTH - 1)
        fillChange({ next: short })
        submit()

        expect(await screen.findByText(PASSWORD_POLICY_MESSAGE, { exact: false })).toBeInTheDocument()
        expect(changePasswordMock).not.toHaveBeenCalled()
    })

    it('rejects a password over the ceiling without calling the API', async () => {
        renderPage()
        openChangeForm()
        const long = 'a'.repeat(PASSWORD_MAX_LENGTH + 1)
        fillChange({ next: long })
        submit()

        expect(await screen.findByText(PASSWORD_POLICY_MESSAGE, { exact: false })).toBeInTheDocument()
        expect(changePasswordMock).not.toHaveBeenCalled()
    })

    it('asks before signing out every device, and does nothing on cancel', () => {
        const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
        renderPage()
        openChangeForm()
        fillChange()
        submit()

        expect(confirmSpy).toHaveBeenCalledTimes(1)
        expect(confirmSpy.mock.calls[0][0]).toMatch(/every device/i)
        expect(changePasswordMock).not.toHaveBeenCalled()
    })

    it('shows the server refusal for a wrong current password and keeps the session', async () => {
        vi.spyOn(window, 'confirm').mockReturnValue(true)
        changePasswordMock.mockRejectedValue(new Error('Current password is incorrect'))
        const onPasswordChanged = vi.fn()
        renderPage({ onPasswordChanged })
        openChangeForm()
        fillChange({ current: 'wrong' })
        submit()

        expect(await screen.findByText(/Current password is incorrect/)).toBeInTheDocument()
        expect(onPasswordChanged).not.toHaveBeenCalled()
    })

    it('shows the lockout once too many attempts have failed', async () => {
        vi.spyOn(window, 'confirm').mockReturnValue(true)
        changePasswordMock.mockRejectedValue(new Error('Too many failed attempts. Try again later.'))
        renderPage()
        openChangeForm()
        fillChange()
        submit()

        expect(await screen.findByText(/Too many failed attempts/)).toBeInTheDocument()
    })

    it('sends the change, then ends the session', async () => {
        vi.spyOn(window, 'confirm').mockReturnValue(true)
        changePasswordMock.mockResolvedValue({ message: 'Password changed successfully' })
        const onPasswordChanged = vi.fn()
        renderPage({ onPasswordChanged })
        openChangeForm()
        fillChange()
        submit()

        await waitFor(() => expect(onPasswordChanged).toHaveBeenCalledTimes(1))
        expect(changePasswordMock).toHaveBeenCalledWith('old-password', 'new-password')
    })

    it('clears the fields when the form is cancelled', () => {
        renderPage()
        openChangeForm()
        fillChange()
        fireEvent.click(screen.getByRole('button', { name: /^cancel$/i }))

        expect(screen.queryByLabelText(/^new password$/i)).toBeNull()
        openChangeForm()
        expect(screen.getByLabelText(/^current password$/i)).toHaveValue('')
    })
})

// Issue #963: the demo account's password is published and every visitor shares
// it. The server refuses these changes; the page does not offer them, and says
// why instead of leaving an empty card.
describe('AccountPage — demo account (issue #963)', () => {
    const demo = { username: 'demo', email: 'demo@example.com', role: 'demo' }

    it('offers neither change password nor delete account', () => {
        renderPage({ user: demo })
        expect(screen.queryByRole('button', { name: /change password/i })).toBeNull()
        expect(screen.queryByRole('button', { name: /delete account/i })).toBeNull()
    })

    it('explains that the demo account is shared and cannot be changed', () => {
        renderPage({ user: demo })
        expect(screen.getByTestId('demo-account-note')).toHaveTextContent(/shared demo account/i)
    })

    it('still offers the walkthrough replay', () => {
        renderPage({ user: demo })
        expect(screen.getByRole('button', { name: /replay/i })).toBeInTheDocument()
    })

    it('shows no demo note to an ordinary user', () => {
        renderPage()
        expect(screen.queryByTestId('demo-account-note')).toBeNull()
    })
})
