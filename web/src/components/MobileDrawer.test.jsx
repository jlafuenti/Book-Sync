import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import MobileDrawer from './MobileDrawer'
import { ThemeProvider } from '../ThemeContext'

// The drawer filters its nav on the signed-in role (issue #208), which comes
// from AuthContext rather than the `user` prop; mirror the prop's role here.
const ROLES = ['user', 'editor', 'admin', 'superadmin']
let currentRole = 'admin'
vi.mock('../contexts/AuthContext', () => ({
    useAuth: () => ({
        hasMinRole: (min) => ROLES.indexOf(currentRole) >= ROLES.indexOf(min),
    }),
}))

/**
 * On a phone the drawer's user block is the *only* way into Account — the
 * bottom nav is full and the desktop sidebar does not exist (issue #146). Play
 * requires an in-app deletion path, and "in-app" includes the installed PWA, so
 * a refactor that turns this block back into a plain `<div>` would quietly
 * strip the deletion route from every mobile user.
 */
function renderDrawer(user = { username: 'alice', role: 'admin' }, onLogout = vi.fn()) {
    currentRole = user?.role ?? 'user'
    render(
        <MemoryRouter>
            <ThemeProvider>
                <MobileDrawer open onClose={vi.fn()} user={user} onLogout={onLogout} />
            </ThemeProvider>
        </MemoryRouter>,
    )
}

describe('MobileDrawer', () => {
    it('shows who is signed in', () => {
        renderDrawer()
        expect(screen.getByText('alice')).toBeInTheDocument()
        expect(screen.getByText('Admin')).toBeInTheDocument()
    })

    it('makes the user block a link to Account', () => {
        renderDrawer()
        const links = screen.getAllByRole('link')
            .filter(a => a.getAttribute('href') === '/account')
        expect(links).toHaveLength(1)
        expect(links[0]).toHaveTextContent('alice')
    })

    it('still renders when the user has not loaded yet', () => {
        renderDrawer(null)
        // Both the name and the role fall back to "User"; the point is that a
        // null user renders rather than throwing on `user.username[0]`.
        expect(screen.getAllByText('User').length).toBeGreaterThan(0)
        expect(screen.getByText('?')).toBeInTheDocument()
    })

    it('offers the main nav destinations', () => {
        renderDrawer()
        for (const label of ['Home', 'Library', 'Series', 'Transcription', 'System']) {
            expect(screen.getByText(label)).toBeInTheDocument()
        }
    })

    it('offers logout', () => {
        renderDrawer({ username: 'alice' }, vi.fn())
        expect(screen.getByText('Logout')).toBeInTheDocument()
    })
})
