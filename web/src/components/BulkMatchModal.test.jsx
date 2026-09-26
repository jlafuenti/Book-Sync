import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import BulkMatchModal from './BulkMatchModal'
import { updateEbookMetadata, updateAudiobookMetadata } from '../api'

vi.mock('../api', () => ({
    updateEbookMetadata: vi.fn(),
    updateAudiobookMetadata: vi.fn(),
    applyRemoteCover: vi.fn(),
}))

// MatchTab does its own searching; this wizard only cares that it is mounted
// per book and that onApply advances. The extra button lets a test trigger
// onApply directly with a fixed payload (issue #730's page_count remap).
vi.mock('./MatchTab', () => ({
    default: ({ currentData, onApply }) => (
        <div data-testid="match-tab">
            {currentData.title}
            <button onClick={() => onApply({ page_count: 412 })}>apply-match</button>
        </div>
    ),
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

// Issue #730: MatchTab's page_count row (ebooks only) applies under the key
// 'page_count', but the ebook's own field/PATCH key is print_page_count.
// EnhancedMetadataModal already remaps this; BulkMatchModal did not, so a
// bulk-match apply with the page count ticked silently dropped the value
// (the server ignores unknown MetadataUpdate fields).
describe('BulkMatchModal page_count remap (issue #730)', () => {
    it('remaps page_count to print_page_count for ebooks', async () => {
        render(<BulkMatchModal books={books} bookType="ebook" onClose={vi.fn()} onUpdate={vi.fn()} />)

        fireEvent.click(screen.getByRole('button', { name: 'apply-match' }))

        await waitFor(() => expect(updateEbookMetadata).toHaveBeenCalled())
        const payload = updateEbookMetadata.mock.calls[0][1]
        expect(payload.print_page_count).toBe(412)
        expect(payload.page_count).toBeUndefined()
    })

    it('drops the page_count key entirely for audiobooks', async () => {
        render(<BulkMatchModal books={books} bookType="audiobook" onClose={vi.fn()} onUpdate={vi.fn()} />)

        fireEvent.click(screen.getByRole('button', { name: 'apply-match' }))

        // page_count is the only field the mocked MatchTab applies here, so
        // once it is dropped there is nothing left to send at all.
        await waitFor(() => expect(screen.getByText('Book 2 of 2')).toBeInTheDocument())
        expect(updateAudiobookMetadata).not.toHaveBeenCalled()
    })
})
