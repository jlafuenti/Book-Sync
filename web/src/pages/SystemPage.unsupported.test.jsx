/**
 * System → Unsupported: converting must report what happened to the sync map.
 *
 * Issue #101 — converting a .mobi re-points its pairs at the new EPUB, and the
 * server now rebuilds their sync maps against that EPUB. When it *can't*
 * (no cached transcript, unreadable file), the conversion still succeeds on
 * disk. Reporting only "Converted & deleted" there is the silent half of the
 * original bug: the pair's coordinates no longer describe its ebook and nothing
 * said so.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

import { UnsupportedFilesTab } from './SystemPage'

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
    filename: 'The Mad Ship.mobi',
    title: 'The Mad Ship',
    author: 'Robin Hobb',
    format: 'mobi',
    file_size: 1024,
    already_converted: false,
    epub_ebook_id: null,
}

beforeEach(() => {
    getUnsupportedFilesMock.mockReset().mockResolvedValue([FILE])
    convertUnsupportedFileMock.mockReset()
    convertAllMock.mockReset()
})

async function renderTab() {
    render(<UnsupportedFilesTab canAdmin={true} />)
    await screen.findByText('The Mad Ship')
}

describe('UnsupportedFilesTab — sync map after conversion', () => {
    it('reports success plainly when the pair was re-aligned', async () => {
        convertUnsupportedFileMock.mockResolvedValue({
            status: 'converted', source_deleted: true, realigned: true, realign_error: null,
        })
        await renderTab()

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
        await renderTab()

        fireEvent.click(screen.getByRole('button', { name: 'Convert & Delete' }))

        const msg = await screen.findByText(/sync map/i)
        expect(msg.textContent).toContain('No cached transcript')
        expect(msg.className).toContain('error')
    })

    it('lists per-pair re-align failures after Convert All', async () => {
        convertAllMock.mockResolvedValue({
            succeeded: ['The Mad Ship.mobi'],
            failed: [],
            total: 1,
            realign_failures: [{ pair_id: 84, error: 'Could not read ebook file: boom' }],
        })
        await renderTab()

        fireEvent.click(screen.getByRole('button', { name: 'Convert All & Delete Original' }))

        await waitFor(() => expect(screen.getByText(/pair 84/i)).toBeTruthy())
        expect(screen.getByText(/pair 84/i).textContent).toContain('Could not read ebook file')
    })
})
