import { describe, it, expect, vi } from 'vitest'
import { useState } from 'react'
import { render, screen, fireEvent } from '@testing-library/react'
import BulkMetadataEditModal from './BulkMetadataEditModal'

// Issue #276: LibraryPage and SeriesPage each carried their own copy of this
// modal — same state, same five `if (field.trim())` lines, same markup. This
// is the one copy, and these tests are what stopped the extraction from being
// a rewrite.

function inputs() {
    return screen.getAllByPlaceholderText('Leave blank to keep unchanged')
}

const AUTHOR = 0
const SERIES = 1
const SERIES_INDEX = 2
const PUBLISHER = 3
const PUBLISHED_YEAR = 4

describe('BulkMetadataEditModal (issue #276)', () => {
    it('renders nothing when closed', () => {
        render(<BulkMetadataEditModal open={false} selectionCount={3} onSave={vi.fn()} onClose={vi.fn()} />)
        expect(screen.queryByRole('dialog')).toBeNull()
    })

    it('is a labelled dialog whose heading counts the selection', () => {
        render(<BulkMetadataEditModal open selectionCount={3} onSave={vi.fn()} onClose={vi.fn()} />)
        const dialog = screen.getByRole('dialog')
        expect(dialog).toHaveAttribute('aria-modal', 'true')
        expect(dialog).toHaveAccessibleName('Edit 3 Items')
        expect(screen.getByRole('button', { name: 'Save to 3 Items' })).toBeInTheDocument()
    })

    it('says "Item" in the singular', () => {
        render(<BulkMetadataEditModal open selectionCount={1} onSave={vi.fn()} onClose={vi.fn()} />)
        expect(screen.getByRole('dialog')).toHaveAccessibleName('Edit 1 Item')
        expect(screen.getByRole('button', { name: 'Save to 1 Item' })).toBeInTheDocument()
    })

    it('shows the caller`s description text', () => {
        render(
            <BulkMetadataEditModal
                open selectionCount={2} onSave={vi.fn()} onClose={vi.fn()}
                description="Leave fields blank to keep existing values."
            />,
        )
        expect(screen.getByText('Leave fields blank to keep existing values.')).toBeInTheDocument()
    })

    it('disables Save while every field is blank, and while saving', () => {
        const { rerender } = render(
            <BulkMetadataEditModal open selectionCount={2} onSave={vi.fn()} onClose={vi.fn()} />,
        )
        expect(screen.getByRole('button', { name: 'Save to 2 Items' })).toBeDisabled()

        fireEvent.change(inputs()[AUTHOR], { target: { value: '   ' } })
        expect(screen.getByRole('button', { name: 'Save to 2 Items' })).toBeDisabled()

        fireEvent.change(inputs()[AUTHOR], { target: { value: 'Robin Hobb' } })
        expect(screen.getByRole('button', { name: 'Save to 2 Items' })).toBeEnabled()

        rerender(<BulkMetadataEditModal open selectionCount={2} saving onSave={vi.fn()} onClose={vi.fn()} />)
        expect(screen.getByRole('button', { name: 'Saving...' })).toBeDisabled()
        expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
    })

    it('renders the caller`s own saving label when it wants one', () => {
        render(
            <BulkMetadataEditModal
                open selectionCount={2} saving onSave={vi.fn()} onClose={vi.fn()}
                savingLabel={<><div className="spinner"></div> Saving...</>}
            />,
        )
        expect(screen.getByRole('button', { name: 'Saving...' }).querySelector('.spinner')).not.toBeNull()
    })

    it('sends a trimmed patch, series_index as a float and published_year as an int', () => {
        const onSave = vi.fn()
        render(<BulkMetadataEditModal open selectionCount={2} onSave={onSave} onClose={vi.fn()} />)

        fireEvent.change(inputs()[AUTHOR], { target: { value: '  Robin Hobb  ' } })
        fireEvent.change(inputs()[SERIES], { target: { value: ' Liveship Traders ' } })
        fireEvent.change(inputs()[SERIES_INDEX], { target: { value: '2.5' } })
        fireEvent.change(inputs()[PUBLISHER], { target: { value: ' Bantam ' } })
        fireEvent.change(inputs()[PUBLISHED_YEAR], { target: { value: '1999' } })
        fireEvent.click(screen.getByRole('button', { name: 'Save to 2 Items' }))

        expect(onSave).toHaveBeenCalledWith({
            author: 'Robin Hobb',
            series: 'Liveship Traders',
            series_index: 2.5,
            publisher: 'Bantam',
            published_year: 1999,
        })
    })

    it('omits every field left blank', () => {
        const onSave = vi.fn()
        render(<BulkMetadataEditModal open selectionCount={1} onSave={onSave} onClose={vi.fn()} />)

        fireEvent.change(inputs()[SERIES], { target: { value: 'Liveship' } })
        fireEvent.click(screen.getByRole('button', { name: 'Save to 1 Item' }))

        expect(onSave).toHaveBeenCalledWith({ series: 'Liveship' })
    })

    it('sends null rather than NaN for a number that will not parse', () => {
        const onSave = vi.fn()
        render(<BulkMetadataEditModal open selectionCount={1} onSave={onSave} onClose={vi.fn()} />)

        // jsdom sanitises a bad value out of `type="number"`, so drive the
        // field the way a paste into a text input would: through the DOM.
        const yearInput = inputs()[PUBLISHED_YEAR]
        yearInput.type = 'text'
        fireEvent.change(yearInput, { target: { value: 'nineteen' } })
        fireEvent.click(screen.getByRole('button', { name: 'Save to 1 Item' }))

        expect(onSave).toHaveBeenCalledWith({ published_year: null })
    })

    it('Cancel and Escape both close without saving', () => {
        const onSave = vi.fn()
        const onClose = vi.fn()
        render(<BulkMetadataEditModal open selectionCount={1} onSave={onSave} onClose={onClose} />)

        fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
        expect(onClose).toHaveBeenCalledTimes(1)

        fireEvent.keyDown(document, { key: 'Escape' })
        expect(onClose).toHaveBeenCalledTimes(2)
        expect(onSave).not.toHaveBeenCalled()
    })

    it('starts blank again the next time it opens', () => {
        function Harness() {
            const [open, setOpen] = useState(true)
            return (
                <div>
                    <button onClick={() => setOpen(true)}>Reopen</button>
                    <BulkMetadataEditModal
                        open={open} selectionCount={1} onSave={vi.fn()} onClose={() => setOpen(false)}
                    />
                </div>
            )
        }
        render(<Harness />)

        fireEvent.change(inputs()[AUTHOR], { target: { value: 'Robin Hobb' } })
        fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
        fireEvent.click(screen.getByRole('button', { name: 'Reopen' }))

        expect(inputs()[AUTHOR]).toHaveValue('')
    })
})
