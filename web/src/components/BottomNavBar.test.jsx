import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import BottomNavBar from './BottomNavBar'

// BottomNavBar is the mobile-only bottom navigation (App renders it only when
// useIsMobile() is true). These assert the buttons that appear in the mobile view.
function renderAt(path) {
    return render(
        <MemoryRouter initialEntries={[path]}>
            <BottomNavBar />
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
