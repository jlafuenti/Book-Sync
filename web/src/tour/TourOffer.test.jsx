import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import TourOffer from './TourOffer'

describe('TourOffer', () => {
    it('renders the title and body copy', () => {
        render(<TourOffer onNotNow={() => {}} onTakeTour={() => {}} />)
        expect(screen.getByText('Take the 5-minute tour?')).toBeInTheDocument()
        expect(screen.getByText(
            'A guided walkthrough of Tandem, using your own library. Quit any time.',
        )).toBeInTheDocument()
    })

    it('calls onNotNow when "Not now" is clicked', () => {
        const onNotNow = vi.fn()
        render(<TourOffer onNotNow={onNotNow} onTakeTour={() => {}} />)
        fireEvent.click(screen.getByText('Not now'))
        expect(onNotNow).toHaveBeenCalledTimes(1)
    })

    it('calls onTakeTour when "Take the tour" is clicked', () => {
        const onTakeTour = vi.fn()
        render(<TourOffer onNotNow={() => {}} onTakeTour={onTakeTour} />)
        fireEvent.click(screen.getByText('Take the tour'))
        expect(onTakeTour).toHaveBeenCalledTimes(1)
    })

    it('calls onNotNow on Escape (Modal\'s default close behaviour)', () => {
        const onNotNow = vi.fn()
        render(<TourOffer onNotNow={onNotNow} onTakeTour={() => {}} />)
        fireEvent.keyDown(document, { key: 'Escape' })
        expect(onNotNow).toHaveBeenCalledTimes(1)
    })

    it('renders as an accessible dialog', () => {
        render(<TourOffer onNotNow={() => {}} onTakeTour={() => {}} />)
        const dialog = screen.getByRole('dialog')
        expect(dialog).toHaveAttribute('aria-modal', 'true')
        expect(dialog).toHaveAccessibleName('Take the 5-minute tour?')
    })
})
