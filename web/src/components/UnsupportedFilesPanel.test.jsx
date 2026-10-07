/**
 * Troubleshoot → Unsupported formats: converting must report what happened to
 * the sync map.
 *
 * Issue #101 — converting a .mobi re-points its pairs at the new EPUB, and the
 * server now rebuilds their sync maps against that EPUB. When it *can't*
 * (no cached transcript, unreadable file), the conversion still succeeds on
 * disk. Reporting only "Converted & deleted" there is the silent half of the
 * original bug: the pair's coordinates no longer describe its ebook and nothing
 * said so.
 *
 * The panel used to be System → Unsupported Files; it moved into Troubleshoot
 * Library's "Unsupported formats" section.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

import UnsupportedFilesPanel from './UnsupportedFilesPanel'

const { getUnsupportedFilesMock, convertUnsupportedFileMock, convertAllMock } = vi.hoisted(() => ({
    getUnsupportedFilesMock: vi.fn(),
    convertUnsupportedFileMock: vi.fn(),
    convertAllMock: vi.fn(),
}))

vi.mock('../api', async (importOriginal) => {
    const actual = await importOriginal()
    return {
        ...actual,
        getUnsupportedFiles: getUnsupportedFilesMock,
        convertUnsupportedFile: convertUnsupportedFileMock,
        convertAllUnsupportedFiles: convertAllMock,
    }
})

const FILE = {
    id: 1743,
    filename: 'Bartleby the Scrivener.mobi',
    title: 'Bartleby the Scrivener',
    author: 'Herman Melville',
    format: 'mobi',
    file_size: 1024,
    already_converted: false,
    epub_ebook_id: null,
}

beforeEach(() => {
    convertUnsupportedFileMock.mockReset()
    convertAllMock.mockReset()
})

async function renderPanel(props = {}) {
    const onChanged = vi.fn()
    const utils = render(<UnsupportedFilesPanel files={[FILE]} canAdmin={true} onChanged={onChanged} {...props} />)
    await screen.findByText('Bartleby the Scrivener')
    return { ...utils, onChanged }
}

describe('UnsupportedFilesPanel — sync map after conversion', () => {
    it('reports success plainly when the pair was re-aligned', async () => {
        convertUnsupportedFileMock.mockResolvedValue({
            status: 'converted', source_deleted: true, realigned: true, realign_error: null,
        })
        await renderPanel()

        fireEvent.click(screen.getByRole('button', { name: 'Convert & Delete' }))

        expect(await screen.findByText('Converted & deleted')).toBeTruthy()
    })

    it('warns when the conversion landed but the sync map could not be rebuilt', async () => {
        convertUnsupportedFileMock.mockResolvedValue({
            status: 'converted',
            source_deleted: true,
            realigned: false,
            realign_error: 'No cached transcript for this pair — run full transcription instead.',
        })
        await renderPanel()

        fireEvent.click(screen.getByRole('button', { name: 'Convert & Delete' }))

        const msg = await screen.findByText(/sync map/i)
        expect(msg.textContent).toContain('No cached transcript')
        expect(msg.className).toContain('error')
    })

    it('lists per-pair re-align failures after Convert All', async () => {
        convertAllMock.mockResolvedValue({
            succeeded: ['Bartleby the Scrivener.mobi'],
            failed: [],
            total: 1,
            realign_failures: [{ pair_id: 84, error: 'Could not read ebook file: boom' }],
        })
        await renderPanel()

        fireEvent.click(screen.getByRole('button', { name: 'Convert All & Delete Original' }))

        await waitFor(() => expect(screen.getByText(/pair 84/i)).toBeTruthy())
        expect(screen.getByText(/pair 84/i).textContent).toContain('Could not read ebook file')
    })

    it('asks the page to reload after a conversion, so Troubleshoot\'s counts follow', async () => {
        convertUnsupportedFileMock.mockResolvedValue({ status: 'converted', realign_error: null })
        const { onChanged } = await renderPanel()

        fireEvent.click(screen.getByRole('button', { name: 'Convert' }))

        await waitFor(() => expect(onChanged).toHaveBeenCalled())
    })
})

describe('UnsupportedFilesPanel permissions', () => {
    it('shows the files but no actions below admin', async () => {
        await renderPanel({ canAdmin: false })

        expect(screen.queryByRole('button', { name: 'Convert All' })).toBeNull()
        expect(screen.queryByRole('button', { name: 'Force Delete' })).toBeNull()
    })

    it('offers Delete Original for a file already converted', async () => {
        render(<UnsupportedFilesPanel files={[{ ...FILE, already_converted: true }]} canAdmin={true} onChanged={() => {}} />)

        expect(await screen.findByRole('button', { name: 'Delete Original' })).toBeInTheDocument()
        expect(screen.queryByRole('button', { name: 'Convert All' })).toBeNull()
    })
})

// Issue #271: the Convert/Delete buttons live in the table's last column, and
// the `.system-card` around this table sets `overflow: hidden`, so on a phone
// that column was simply unreachable — no scroll, no wrap, no fallback.
describe('UnsupportedFilesPanel mobile layout', () => {
    it('renders the file table inside a horizontal-scroll wrapper', async () => {
        await renderPanel()

        const table = screen.getByRole('table')
        expect(table.closest('.table-wrapper')).not.toBeNull()
    })
})

// Issue #279: the force-delete confirmation is the shared Modal primitive.
describe('UnsupportedFilesPanel force-delete confirmation is a dialog', () => {
    it('is labelled, names the file, and Escape closes it with focus restored', async () => {
        await renderPanel()

        const trigger = screen.getAllByRole('button', { name: 'Force Delete' })[0]
        trigger.focus()
        fireEvent.click(trigger)

        const dialog = screen.getByRole('dialog')
        expect(dialog).toHaveAttribute('aria-modal', 'true')
        expect(dialog).toHaveAccessibleName('Confirm Force Delete')
        expect(dialog).toHaveTextContent('Bartleby the Scrivener.mobi')
        expect(dialog.contains(document.activeElement)).toBe(true)

        fireEvent.keyDown(document, { key: 'Escape' })
        expect(screen.queryByRole('dialog')).toBeNull()
        expect(document.activeElement).toBe(trigger)
    })

    it('closes on a backdrop click, the way it always did', async () => {
        const { container } = await renderPanel()

        fireEvent.click(screen.getAllByRole('button', { name: 'Force Delete' })[0])
        expect(screen.getByRole('dialog')).toBeInTheDocument()

        fireEvent.click(container.querySelector('.modal-overlay'))
        expect(screen.queryByRole('dialog')).toBeNull()
    })
})
