import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import MobileDrawer from './MobileDrawer'
import { AuthProvider } from '../contexts/AuthContext'

// The drawer footer renders the theme picker, which needs the theme context.
// Nothing here is about theming, so stub it rather than nest another provider.
vi.mock('./ThemePicker', () => ({ ThemePicker: () => null }))

// The drawer is the hamburger menu on mobile. It is a third copy of the same
// navigation list — sidebar (App.jsx), bottom tab bar (BottomNavBar) and this —
// and it was the one still offering System to everybody (issues #283, #208).
function renderDrawer(role, path = '/continue') {
    const user = { username: 'x', role }
    return render(
        <MemoryRouter initialEntries={[path]}>
            <AuthProvider user={user}>
                <MobileDrawer open onClose={vi.fn()} user={user} onLogout={vi.fn()} />
            </AuthProvider>
        </MemoryRouter>,
    )
}

describe('MobileDrawer — the System entry follows the route gate', () => {
    it('hides System from a plain user', () => {
        renderDrawer('user')
        expect(screen.queryByText('System')).toBeNull()
    })

    it('still shows a plain user everything they can open', () => {
        renderDrawer('user')
        for (const label of ['Home', 'Library', 'Series', 'Transcription']) {
            expect(screen.getByText(label)).toBeInTheDocument()
        }
    })

    it('shows System to an editor', () => {
        renderDrawer('editor')
        expect(screen.getByText('System')).toBeInTheDocument()
    })

    it('points an editor at the page they can actually open', () => {
        // /system/status is admin-only, so sending an editor there lands them on
        // a redirect back to Home — which reads as a broken link rather than as
        // a permission boundary.
        renderDrawer('editor')
        expect(screen.getByText('System').closest('a').getAttribute('href'))
            .toBe('/system/troubleshoot')
    })

    it('points an admin at the status page', () => {
        renderDrawer('admin')
        expect(screen.getByText('System').closest('a').getAttribute('href'))
            .toBe('/system/status')
    })

    it('marks System active while anywhere under /system', () => {
        renderDrawer('admin', '/system/troubleshoot')
        expect(screen.getByText('System').closest('a')).toHaveClass('active')
    })
})
