import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import GoogleBooksSettings from './GoogleBooksSettings'

/**
 * Issue #739: the Google Books API key on the System page. Used by the Match
 * search and the print page lookup. Stored like the Hardcover token: masked
 * on read, the placeholder meaning "unchanged".
 */

const { getSettingsMock, updateSettingsMock, testMock } = vi.hoisted(() => ({
    getSettingsMock: vi.fn(),
    updateSettingsMock: vi.fn(),
    testMock: vi.fn(),
}))

vi.mock('../api', async (importOriginal) => {
    const actual = await importOriginal()
    return {
        ...actual,
        getSettings: getSettingsMock,
        updateSettings: updateSettingsMock,
        testGoogleBooksKey: testMock,
    }
})

beforeEach(() => {
    getSettingsMock.mockReset().mockResolvedValue({ google_books_api_key: '', google_books_key_from_env: false })
    updateSettingsMock.mockReset().mockResolvedValue({})
    testMock.mockReset().mockResolvedValue({ success: true })
})

const keyInput = () => screen.findByLabelText('Google Books API key')

describe('Google Books API key', () => {
    it('shows a saved key masked, in a password field', async () => {
        getSettingsMock.mockResolvedValue({ google_books_api_key: '********', google_books_key_from_env: false })
        render(<GoogleBooksSettings />)
        const input = await keyInput()
        await waitFor(() => expect(input).toHaveValue('********'))
        expect(input).toHaveAttribute('type', 'password')
    })

    it('saves the key', async () => {
        render(<GoogleBooksSettings />)
        fireEvent.change(await keyInput(), { target: { value: 'my-key' } })
        fireEvent.click(screen.getByRole('button', { name: 'Save' }))
        await waitFor(() => expect(updateSettingsMock).toHaveBeenCalledWith({ google_books_api_key: 'my-key' }))
        expect(await screen.findByText('Saved')).toBeInTheDocument()
    })

    it('says when saving failed', async () => {
        updateSettingsMock.mockRejectedValue(new Error('nope'))
        render(<GoogleBooksSettings />)
        fireEvent.click(await screen.findByRole('button', { name: 'Save' }))
        expect(await screen.findByText('Failed to save')).toBeInTheDocument()
    })

    it('tests a typed key, and the saved one when the field shows the mask', async () => {
        getSettingsMock.mockResolvedValue({ google_books_api_key: '********', google_books_key_from_env: false })
        render(<GoogleBooksSettings />)
        const input = await keyInput()
        await waitFor(() => expect(input).toHaveValue('********'))

        fireEvent.click(screen.getByRole('button', { name: 'Test Key' }))
        await waitFor(() => expect(testMock).toHaveBeenCalledWith(''))
        expect(await screen.findByText(/google accepted the key/i)).toBeInTheDocument()

        fireEvent.change(input, { target: { value: 'typed-key' } })
        fireEvent.click(screen.getByRole('button', { name: 'Test Key' }))
        await waitFor(() => expect(testMock).toHaveBeenLastCalledWith('typed-key'))
    })

    it("shows the server's reason when the test fails", async () => {
        testMock.mockRejectedValue(new Error('Google rejected the key.'))
        render(<GoogleBooksSettings />)
        fireEvent.click(await screen.findByRole('button', { name: 'Test Key' }))
        expect(await screen.findByText(/google rejected the key/i)).toBeInTheDocument()
    })

    it('says when the server already has a key from its environment', async () => {
        getSettingsMock.mockResolvedValue({ google_books_api_key: '', google_books_key_from_env: true })
        render(<GoogleBooksSettings />)
        expect(await screen.findByText(/already has a key from GOOGLE_BOOKS_API_KEY/i)).toBeInTheDocument()
    })

    it('explains which Google API the key needs, on tap as well as hover', async () => {
        render(<GoogleBooksSettings />)
        const info = await screen.findByRole('button', { name: /which google api does the key need/i })
        expect(screen.queryByRole('tooltip')).toBeNull()

        fireEvent.click(info)
        const tip = screen.getByRole('tooltip')
        expect(tip).toHaveTextContent(/Books API/)
        expect(tip).toHaveTextContent(/books\.googleapis\.com/)
        expect(tip).toHaveTextContent(/HTTP referrer/i)
        expect(info).toHaveAttribute('aria-expanded', 'true')
        expect(info).toHaveAttribute('aria-describedby', tip.id)

        fireEvent.click(info)
        expect(screen.queryByRole('tooltip')).toBeNull()

        fireEvent.mouseEnter(info)
        expect(screen.getByRole('tooltip')).toBeInTheDocument()
        fireEvent.mouseLeave(info)
        expect(screen.queryByRole('tooltip')).toBeNull()
    })

    it('closes the tooltip with Escape', async () => {
        render(<GoogleBooksSettings />)
        const info = await screen.findByRole('button', { name: /which google api does the key need/i })
        fireEvent.focus(info)
        expect(screen.getByRole('tooltip')).toBeInTheDocument()
        fireEvent.keyDown(info, { key: 'Escape' })
        expect(screen.queryByRole('tooltip')).toBeNull()
    })
})
