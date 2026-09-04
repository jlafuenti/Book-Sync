import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import LoginPage from './LoginPage'

// Issue #211. This is the first screen every internet-facing user meets and it
// had no test at all (2.54 % lines, 0 functions in the coverage baseline), so a
// regression that locks everyone out would have shipped green.

const { loginMock, registerMock, getMeMock } = vi.hoisted(() => ({
    loginMock: vi.fn(),
    registerMock: vi.fn(),
    getMeMock: vi.fn(),
}))

vi.mock('../api', () => ({
    login: loginMock,
    register: registerMock,
    getMe: getMeMock,
}))

beforeEach(() => {
    loginMock.mockReset()
    registerMock.mockReset()
    getMeMock.mockReset()
})

function fillAndSubmit({ username = 'alice', password = 'hunter2', email } = {}) {
    fireEvent.change(screen.getByLabelText('Username'), { target: { value: username } })
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: password } })
    if (email !== undefined) {
        fireEvent.change(screen.getByLabelText('Email'), { target: { value: email } })
    }
    fireEvent.click(screen.getByRole('button'))
}

describe('LoginPage — sign in', () => {
    it('logs in, loads the profile, and hands the user to onLogin', async () => {
        loginMock.mockResolvedValue({ access_token: 'a', refresh_token: 'r' })
        getMeMock.mockResolvedValue({ username: 'alice', role: 'admin' })
        const onLogin = vi.fn()

        render(<LoginPage onLogin={onLogin} />)
        fillAndSubmit()

        await waitFor(() => expect(onLogin).toHaveBeenCalledWith({ username: 'alice', role: 'admin' }))
        expect(loginMock).toHaveBeenCalledWith('alice', 'hunter2')
        expect(getMeMock).toHaveBeenCalledTimes(1)
    })

    it('renders the failure message and does not sign the user in', async () => {
        loginMock.mockRejectedValue(new Error('Invalid username or password'))
        const onLogin = vi.fn()

        render(<LoginPage onLogin={onLogin} />)
        fillAndSubmit()

        expect(await screen.findByText(/Invalid username or password/)).toBeTruthy()
        expect(getMeMock).not.toHaveBeenCalled()
        expect(onLogin).not.toHaveBeenCalled()
    })

    it('disables the submit button while the request is in flight', async () => {
        let release
        loginMock.mockImplementation(() => new Promise((resolve) => { release = resolve }))

        render(<LoginPage onLogin={vi.fn()} />)
        const button = screen.getByRole('button')
        fillAndSubmit()

        await waitFor(() => expect(button.disabled).toBe(true))

        getMeMock.mockResolvedValue({ username: 'alice' })
        release({})
        await waitFor(() => expect(button.disabled).toBe(false))
    })
})

describe('LoginPage — request access', () => {
    it('switches to the register form and asks for an email', () => {
        render(<LoginPage onLogin={vi.fn()} />)
        expect(screen.queryByLabelText('Email')).toBeNull()

        fireEvent.click(screen.getByText('Request Access'))

        expect(screen.getByLabelText('Email')).toBeTruthy()
        expect(screen.getByText('Request an account')).toBeTruthy()
    })

    it('shows the approval notice, clears the fields and returns to sign-in', async () => {
        registerMock.mockResolvedValue({ message: 'Access request submitted.' })

        render(<LoginPage onLogin={vi.fn()} />)
        fireEvent.click(screen.getByText('Request Access'))
        fillAndSubmit({ email: 'alice@example.com' })

        expect(await screen.findByText(/Access request submitted\./)).toBeTruthy()
        expect(registerMock).toHaveBeenCalledWith('alice', 'alice@example.com', 'hunter2')
        // Back on the sign-in form, with nothing left in the inputs.
        expect(screen.getByLabelText('Username').value).toBe('')
        expect(screen.getByLabelText('Password').value).toBe('')
        expect(screen.queryByLabelText('Email')).toBeNull()
    })

    it('renders a rejected registration as an error', async () => {
        registerMock.mockRejectedValue(new Error('Username already exists'))

        render(<LoginPage onLogin={vi.fn()} />)
        fireEvent.click(screen.getByText('Request Access'))
        fillAndSubmit({ email: 'alice@example.com' })

        expect(await screen.findByText(/Username already exists/)).toBeTruthy()
    })

    it('clears a stale error when the mode is switched', async () => {
        loginMock.mockRejectedValue(new Error('Invalid username or password'))

        render(<LoginPage onLogin={vi.fn()} />)
        fillAndSubmit()
        expect(await screen.findByText(/Invalid username or password/)).toBeTruthy()

        fireEvent.click(screen.getByText('Request Access'))

        expect(screen.queryByText(/Invalid username or password/)).toBeNull()
    })
})

describe('LoginPage - account recovery', () => {
    // Issue #204: there is no self-service reset, and the screen used to say
    // nothing at all about what a locked-out user should do.
    it('points a user who forgot their password at their administrator', () => {
        render(<LoginPage onLogin={vi.fn()} />)

        expect(screen.getByText(/forgot your password/i).textContent)
            .toMatch(/administrator/i)
    })

    it('does not show the recovery hint on the request-access form', () => {
        render(<LoginPage onLogin={vi.fn()} />)
        fireEvent.click(screen.getByText('Request Access'))

        expect(screen.queryByText(/forgot your password/i)).toBeNull()
    })
})
