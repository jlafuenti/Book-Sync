import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import AccountDeletionPage from './AccountDeletionPage'

/**
 * The public "web link resource" Google Play requires (issue #146).
 *
 * Its whole reason to exist is that it works for someone who has *already
 * uninstalled the app* — and who therefore has no session, and may not be able
 * to sign in at all. So the two things pinned here are that it renders with no
 * token and no API of any kind, and that it names the fallback (ask the server
 * operator) as well as the self-service route.
 *
 * Importing `../api` at all would defeat the point, so the module is mocked to
 * throw: if the page ever grows a call, this fails rather than shipping a page
 * that 401s for the people it is for.
 */

vi.mock('../api', () => new Proxy({}, {
    get() { throw new Error('the public deletion page must not call the API') },
}))

function renderPage() {
    return render(
        <MemoryRouter>
            <AccountDeletionPage />
        </MemoryRouter>,
    )
}

describe('AccountDeletionPage (public)', () => {
    it('renders with no session', () => {
        renderPage()
        expect(screen.getByRole('heading', { name: /delete your tandem account/i }))
            .toBeInTheDocument()
    })

    it('spells out the in-app route', () => {
        renderPage()
        expect(screen.getByTestId('deletion-steps').textContent).toMatch(/account/i)
        expect(screen.getByTestId('deletion-steps').textContent).toMatch(/delete account/i)
    })

    it('names the fallback for someone who can no longer sign in', () => {
        renderPage()
        expect(screen.getByTestId('deletion-operator-fallback').textContent)
            .toMatch(/operator|admin|whoever runs/i)
    })

    it('says what is deleted and what is not', () => {
        renderPage()
        const what = screen.getByTestId('deletion-what-goes').textContent
        expect(what).toMatch(/bookmark|position/i)
        expect(what).toMatch(/audit/i)
    })

    it('offers a way back to the app for someone who is still signed in', () => {
        renderPage()
        expect(screen.getByRole('link', { name: /sign in/i })).toHaveAttribute('href', '/')
    })
})
