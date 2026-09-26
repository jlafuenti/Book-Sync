import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import EnhancedMetadataModal from './EnhancedMetadataModal'
import { formatDateTime } from '../lib/datetime'

const { enrichAudiobookFromAbsMock } = vi.hoisted(() => ({
    enrichAudiobookFromAbsMock: vi.fn(),
}))

vi.mock('../api', () => ({
    uploadEbookCover: vi.fn(),
    uploadAudiobookCover: vi.fn(),
    applyRemoteCover: vi.fn(),
    rescanBook: vi.fn(),
    enrichAudiobookFromAbs: enrichAudiobookFromAbsMock,
}))

// MatchTab's own search/compare behavior (including the page_count row) is
// covered in MatchTab.test.jsx. Here it's stubbed to a button that fires
// onApply directly, so this file only exercises how the modal maps that
// payload into its own form state (Ruling R1).
vi.mock('./MatchTab', () => ({
    default: ({ onApply }) => (
        <button onClick={() => onApply({ page_count: 412 })}>apply-match</button>
    ),
}))

const book = { id: 1538, title: 'Antiagon Fire', author: 'L. E. Modesitt Jr' }

beforeEach(() => {
    enrichAudiobookFromAbsMock.mockReset()
    vi.spyOn(window, 'alert').mockImplementation(() => {})
})

// Issue #216: `uploaded_at` is naive UTC on the wire, so the Files tab's
// "Added" row must be parsed as UTC rather than as browser-local time.
describe('EnhancedMetadataModal file info timestamps (issue #216)', () => {
    it('renders "Added" at the UTC instant the server meant', () => {
        render(
            <EnhancedMetadataModal
                book={{ ...book, uploaded_at: '2026-08-22T14:03:00' }}
                type="audiobook" initialTab="Files" onClose={vi.fn()} onSave={vi.fn()}
            />,
        )

        const added = screen.getByText('Added').nextSibling.textContent
        expect(added).toBe(formatDateTime('2026-08-22T14:03:00', {
            year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
        }))
    })
})

describe('EnhancedMetadataModal enrich from ABS', () => {
    it('alerts about the tag-write failure and does not claim the file was updated', async () => {
        enrichAudiobookFromAbsMock.mockResolvedValue({
            status: 'tag_write_failed',
            message: "Metadata updated in the library, but writing tags to the file failed: 'utf-8' codec can't decode byte 0xc4",
            tag_write_error: "'utf-8' codec can't decode byte 0xc4",
        })
        const onClose = vi.fn()
        render(<EnhancedMetadataModal book={book} type="audiobook" onClose={onClose} onSave={vi.fn()} />)

        fireEvent.click(screen.getByRole('button', { name: /Enrich from ABS/ }))

        await waitFor(() => expect(window.alert).toHaveBeenCalled())
        const alertText = window.alert.mock.calls[0][0]
        expect(alertText).toMatch(/tag|file/i)
        expect(alertText).not.toMatch(/^Enriched from Audiobookshelf$/)
    })

    it('alerts success and closes the modal when enrichment and tag write both succeed', async () => {
        enrichAudiobookFromAbsMock.mockResolvedValue({
            status: 'enriched',
            message: 'Metadata enriched from Audiobookshelf and written back to file.',
            tag_write_error: null,
        })
        const onClose = vi.fn()
        render(<EnhancedMetadataModal book={book} type="audiobook" onClose={onClose} onSave={vi.fn()} />)

        fireEvent.click(screen.getByRole('button', { name: /Enrich from ABS/ }))

        await waitFor(() => expect(onClose).toHaveBeenCalled())
    })
})

// Issue #279: adopted the shared Modal primitive. This dialog sits over an
// unsaved form, so backdrop click stays disabled — it never closed that way.
describe('EnhancedMetadataModal dialog semantics (issue #279)', () => {
    it('is a labelled dialog that closes on Escape but not on backdrop click', () => {
        const onClose = vi.fn()
        const { container } = render(
            <EnhancedMetadataModal book={book} type="ebook" onClose={onClose} onSave={vi.fn()} />,
        )

        const dialog = screen.getByRole('dialog')
        expect(dialog).toHaveAttribute('aria-modal', 'true')
        expect(dialog).toHaveAccessibleName('Edit Ebook')
        expect(dialog).toHaveClass('modal', 'modal-xl')

        fireEvent.click(container.querySelector('.modal-overlay'))
        expect(onClose).not.toHaveBeenCalled()

        fireEvent.keyDown(document, { key: 'Escape' })
        expect(onClose).toHaveBeenCalledTimes(1)
    })
})

// Issue #730: an ebook's printed page count, editable on the Details tab and
// fillable from a Google Books match (Ruling R1).
describe('EnhancedMetadataModal print page count (issue #730)', () => {
    it('renders "Print pages" holding the current count and saves it as a number', async () => {
        const onSave = vi.fn().mockResolvedValue({})
        render(
            <EnhancedMetadataModal
                book={{ ...book, print_page_count: 412 }}
                type="ebook" onClose={vi.fn()} onSave={onSave}
            />,
        )
        const input = screen.getByText('Print pages').nextElementSibling
        expect(input).toHaveValue(412)

        fireEvent.click(screen.getByRole('button', { name: /Save Details/ }))

        await waitFor(() => expect(onSave).toHaveBeenCalled())
        const payload = onSave.mock.calls[0][1]
        expect(payload.print_page_count).toBe(412)
        expect(typeof payload.print_page_count).toBe('number')
    })

    it('has no Print pages input for an audiobook', () => {
        render(
            <EnhancedMetadataModal book={book} type="audiobook" onClose={vi.fn()} onSave={vi.fn()} />,
        )
        expect(screen.queryByText('Print pages')).toBeNull()
    })

    it('clearing the input saves print_page_count: 0', async () => {
        const onSave = vi.fn().mockResolvedValue({})
        render(
            <EnhancedMetadataModal
                book={{ ...book, print_page_count: 412 }}
                type="ebook" onClose={vi.fn()} onSave={onSave}
            />,
        )
        const input = screen.getByText('Print pages').nextElementSibling
        fireEvent.change(input, { target: { value: '' } })

        fireEvent.click(screen.getByRole('button', { name: /Save Details/ }))

        await waitFor(() => expect(onSave).toHaveBeenCalled())
        expect(onSave.mock.calls[0][1].print_page_count).toBe(0)
    })

    it("maps a Match tab result's page_count into the form's print_page_count", () => {
        render(
            <EnhancedMetadataModal
                book={{ ...book, print_page_count: null }}
                type="ebook" initialTab="Match" onClose={vi.fn()} onSave={vi.fn()}
            />,
        )
        fireEvent.click(screen.getByRole('button', { name: 'apply-match' }))

        const input = screen.getByText('Print pages').nextElementSibling
        expect(input).toHaveValue(412)
    })
})
