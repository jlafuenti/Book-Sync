import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import BottomNavBar from './BottomNavBar'
import { AuthProvider } from '../contexts/AuthContext'

// BottomNavBar is the mobile-only bottom navigation (App renders it only when
// useIsMobile() is true). These assert the buttons that appear in the mobile view.
function renderAt(path, role = 'admin') {
    return render(
        <MemoryRouter initialEntries={[path]}>
            <AuthProvider user={{ username: 'x', role }}>
                <BottomNavBar />
            </AuthProvider>
        </MemoryRouter>,
    )
}

describe('BottomNavBar (mobile navigation)', () => {
    it('renders all five mobile nav destinations', () => {
        renderAt('/continue')
        for (const label of ['Home', 'Library', 'Series', 'Transcription', 'Admin']) {
            expect(screen.getByText(label)).toBeInTheDocument()
        }
    })

    it('marks the active tab based on the current route', () => {
        renderAt('/library')
        const active = screen.getByText('Library').closest('a')
        expect(active).toHaveClass('active')
        const inactive = screen.getByText('Series').closest('a')
        expect(inactive).not.toHaveClass('active')
    })
})

describe('BottomNavBar — the Admin tab follows the route gate', () => {
    // The /system routes are editor-or-admin depending on the tab (issue #283).
    // Leaving the tab visible to everyone would send a plain user straight into
    // a redirect, which reads as the app being broken rather than as a
    // permission boundary.
    it('hides Admin from a plain user', () => {
        renderAt('/continue', 'user')
        expect(screen.queryByText('Admin')).toBeNull()
        expect(screen.getByText('Library')).toBeInTheDocument()
    })

    it('shows Admin to an editor, who has Troubleshoot and Unsupported', () => {
        renderAt('/continue', 'editor')
        expect(screen.getByText('Admin')).toBeInTheDocument()
    })

    it('points an editor at a page they can actually open', () => {
        renderAt('/continue', 'editor')
        expect(screen.getByText('Admin').closest('a').getAttribute('href'))
            .toBe('/system/troubleshoot')
    })

    it('points an admin at the status page', () => {
        renderAt('/continue', 'admin')
        expect(screen.getByText('Admin').closest('a').getAttribute('href'))
            .toBe('/system/status')
    })
})
