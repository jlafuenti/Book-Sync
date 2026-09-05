import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import LoginPage from './LoginPage'

// Issue #210. The login page used to offer "Request Access" unconditionally, so
// an operator who closed registration got a confusing 403 after the visitor had
// already filled the form in. It now asks the server first.

const { loginMock, registerMock, getMeMock, getRegistrationModeMock } = vi.hoisted(() => ({
    loginMock: vi.fn(),
    registerMock: vi.fn(),
    getMeMock: vi.fn(),
    getRegistrationModeMock: vi.fn(),
}))

vi.mock('../api', () => ({
    login: loginMock,
    register: registerMock,
    getMe: getMeMock,
    getRegistrationMode: getRegistrationModeMock,
}))

beforeEach(() => {
    loginMock.mockReset()
    registerMock.mockReset()
    getMeMock.mockReset()
    getRegistrationModeMock.mockReset()
    getRegistrationModeMock.mockResolvedValue('open')
})

const renderPage = async () => {
    render(<LoginPage onLogin={vi.fn()} />)
    await waitFor(() => expect(getRegistrationModeMock).toHaveBeenCalled())
}

describe('LoginPage — open mode', () => {
    it('offers Request Access, exactly as before', async () => {
        await renderPage()
        expect(await screen.findByText('Request Access')).toBeTruthy()
    })

    it('does not ask for an invite code', async () => {
        await renderPage()
        fireEvent.click(screen.getByText('Request Access'))
        expect(screen.queryByLabelText(/invite code/i)).toBeNull()
    })
})

describe('LoginPage — invite mode', () => {
    beforeEach(() => getRegistrationModeMock.mockResolvedValue('invite'))

    it('still offers Request Access', async () => {
        await renderPage()
        expect(await screen.findByText('Request Access')).toBeTruthy()
    })

    it('asks for an invite code, and requires it', async () => {
        await renderPage()
        fireEvent.click(await screen.findByText('Request Access'))

        const field = screen.getByLabelText(/invite code/i)
        expect(field).toBeTruthy()
        expect(field.required).toBe(true)
    })

    it('sends the code with the request', async () => {
        registerMock.mockResolvedValue({ message: 'Access request submitted.' })
        await renderPage()
        fireEvent.click(await screen.findByText('Request Access'))

        fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'alice' } })
        fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'alice@example.com' } })
        fireEvent.change(screen.getByLabelText(/invite code/i), { target: { value: 'CODE-123' } })
        fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'hunter22' } })
        fireEvent.click(screen.getByRole('button', { name: /request access/i }))

        await waitFor(() => expect(registerMock).toHaveBeenCalledWith(
            'alice', 'alice@example.com', 'hunter22', 'CODE-123',
        ))
    })

    it('clears the code along with the other fields after a submit', async () => {
        registerMock.mockResolvedValue({ message: 'Access request submitted.' })
        await renderPage()
        fireEvent.click(await screen.findByText('Request Access'))

        fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'alice' } })
        fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'alice@example.com' } })
        fireEvent.change(screen.getByLabelText(/invite code/i), { target: { value: 'CODE-123' } })
        fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'hunter22' } })
        fireEvent.click(screen.getByRole('button', { name: /request access/i }))

        expect(await screen.findByText(/Access request submitted/)).toBeTruthy()
        fireEvent.click(screen.getByText('Request Access'))
        expect(screen.getByLabelText(/invite code/i).value).toBe('')
    })

    it('keeps the terms link (issue #262)', async () => {
        await renderPage()
        fireEvent.click(await screen.findByText('Request Access'))

        const link = screen.getByRole('link', { name: /terms/i })
        expect(link.getAttribute('href')).toBe('/terms')
    })
})

describe('LoginPage — closed mode', () => {
    beforeEach(() => getRegistrationModeMock.mockResolvedValue('closed'))

    it('hides the request form and says who to ask', async () => {
        await renderPage()

        expect(await screen.findByText(/ask your administrator for an account/i)).toBeTruthy()
        expect(screen.queryByText('Request Access')).toBeNull()
    })

    it('still lets an existing user sign in', async () => {
        loginMock.mockResolvedValue({})
        getMeMock.mockResolvedValue({ username: 'alice' })
        const onLogin = vi.fn()

        render(<LoginPage onLogin={onLogin} />)
        await waitFor(() => expect(getRegistrationModeMock).toHaveBeenCalled())

        fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'alice' } })
        fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'hunter22' } })
        fireEvent.click(screen.getByRole('button', { name: /sign in/i }))

        await waitFor(() => expect(onLogin).toHaveBeenCalled())
    })
})

describe('LoginPage — the mode lookup fails', () => {
    it('falls back to offering the request form', async () => {
        // A server too old to have the endpoint, or one that is briefly
        // unreachable. Hiding the form on an error would lock a whole
        // deployment out of requesting accounts over a transient 500.
        getRegistrationModeMock.mockRejectedValue(new Error('boom'))
        await renderPage()

        expect(await screen.findByText('Request Access')).toBeTruthy()
    })
})
