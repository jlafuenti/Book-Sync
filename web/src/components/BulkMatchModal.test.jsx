import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import BulkMatchModal from './BulkMatchModal'

vi.mock('../api', () => ({
    updateEbookMetadata: vi.fn(),
    updateAudiobookMetadata: vi.fn(),
    applyRemoteCover: vi.fn(),
}))

// MatchTab does its own searching; this wizard only cares that it is mounted
// per book and that onApply advances.
vi.mock('./MatchTab', () => ({
    default: ({ currentData }) => <div data-testid="match-tab">{currentData.title}</div>,
}))

const books = [
    { id: 1, title: 'Antiagon Fire', author: 'Modesitt' },
    { id: 2, title: 'Madness in Solidar' },
]

beforeEach(() => vi.clearAllMocks())

describe('BulkMatchModal (issue #279)', () => {
    it('is a labelled dialog naming the book it is on', () => {
        render(<BulkMatchModal books={books} bookType="ebook" onClose={vi.fn()} onUpdate={vi.fn()} />)
        const dialog = screen.getByRole('dialog')
        expect(dialog).toHaveAttribute('aria-modal', 'true')
        expect(dialog).toHaveAccessibleName('Antiagon Fire')
        expect(screen.getByText('Book 1 of 2')).toBeInTheDocument()
    })

    it('closes on Escape and on ✕ Cancel', () => {
        const onClose = vi.fn()
        render(<BulkMatchModal books={books} bookType="ebook" onClose={onClose} onUpdate={vi.fn()} />)

        fireEvent.keyDown(document, { key: 'Escape' })
        expect(onClose).toHaveBeenCalledTimes(1)

        fireEvent.click(screen.getByRole('button', { name: '✕ Cancel' }))
        expect(onClose).toHaveBeenCalledTimes(2)
    })

    it('Skip walks to the next book and then to the done panel', () => {
        render(<BulkMatchModal books={books} bookType="ebook" onClose={vi.fn()} onUpdate={vi.fn()} />)

        fireEvent.click(screen.getByRole('button', { name: 'Skip →' }))
        expect(screen.getByText('Book 2 of 2')).toBeInTheDocument()
        expect(screen.getByTestId('match-tab')).toHaveTextContent('Madness in Solidar')

        fireEvent.click(screen.getByRole('button', { name: 'Skip →' }))
        expect(screen.getByRole('dialog')).toHaveAccessibleName('Done — matched 0 of 2 books')
        expect(screen.getByRole('button', { name: 'Close' })).toBeInTheDocument()
    })
})
