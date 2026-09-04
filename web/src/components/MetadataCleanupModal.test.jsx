import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import MetadataCleanupModal from './MetadataCleanupModal'

// Issue #279 asks for this file *before* the modal is migrated onto the shared
// Modal primitive, so the migration is provably a no-behaviour-change refactor.

const {
    getMetadataDiscrepanciesMock,
    resolveMetadataDiscrepanciesMock,
    ignoreMetadataDiscrepanciesMock,
    deletePairMock,
} = vi.hoisted(() => ({
    getMetadataDiscrepanciesMock: vi.fn(),
    resolveMetadataDiscrepanciesMock: vi.fn(),
    ignoreMetadataDiscrepanciesMock: vi.fn(),
    deletePairMock: vi.fn(),
}))

vi.mock('../api', () => ({
    getMetadataDiscrepancies: getMetadataDiscrepanciesMock,
    resolveMetadataDiscrepancies: resolveMetadataDiscrepanciesMock,
    ignoreMetadataDiscrepancies: ignoreMetadataDiscrepanciesMock,
    deletePair: deletePairMock,
    getCoverUrl: vi.fn(() => ''),
}))

vi.mock('./CoverImg', () => ({ default: () => <img alt="cover" /> }))

const pair = {
    pair_id: 7,
    title: 'Antiagon Fire',
    discrepancies: [
        { field: 'author', ebook_value: 'L. E. Modesitt Jr', audiobook_value: 'Modesitt' },
        { field: 'publisher', ebook_value: 'Tor', audiobook_value: '' },
    ],
}

beforeEach(() => {
    getMetadataDiscrepanciesMock.mockReset().mockResolvedValue([pair])
    resolveMetadataDiscrepanciesMock.mockReset().mockResolvedValue({})
    ignoreMetadataDiscrepanciesMock.mockReset().mockResolvedValue({})
    deletePairMock.mockReset().mockResolvedValue({})
})

describe('MetadataCleanupModal', () => {
    it('loads discrepancies and shows the first pair', async () => {
        render(<MetadataCleanupModal onClose={vi.fn()} onComplete={vi.fn()} />)
        expect(await screen.findByText('Resolving Pair 1 of 1')).toBeInTheDocument()
        expect(screen.getByText('Antiagon Fire')).toBeInTheDocument()
        expect(screen.getByText('✨ Clean Up Metadata')).toBeInTheDocument()
    })

    it('shows the empty state when nothing is mismatched', async () => {
        getMetadataDiscrepanciesMock.mockResolvedValue([])
        const onClose = vi.fn()
        render(<MetadataCleanupModal onClose={onClose} onComplete={vi.fn()} />)
        expect(await screen.findByText('All Clean! 🧹')).toBeInTheDocument()
        fireEvent.click(screen.getByRole('button', { name: 'Done' }))
        expect(onClose).toHaveBeenCalled()
    })

    it('surfaces a load failure', async () => {
        getMetadataDiscrepanciesMock.mockRejectedValue(new Error('boom'))
        render(<MetadataCleanupModal onClose={vi.fn()} onComplete={vi.fn()} />)
        expect(await screen.findByText('⚠️ boom')).toBeInTheDocument()
    })

    it('sends the picked side as an update and ignores the untouched field', async () => {
        const onComplete = vi.fn()
        render(<MetadataCleanupModal onClose={vi.fn()} onComplete={onComplete} />)
        await screen.findByText('Resolving Pair 1 of 1')

        // Pick the ebook's author -> the audiobook is the one that changes.
        fireEvent.click(screen.getByText('L. E. Modesitt Jr'))
        fireEvent.click(screen.getByRole('button', { name: 'Save & Next' }))

        await waitFor(() => expect(resolveMetadataDiscrepanciesMock).toHaveBeenCalledWith(7, {
            ebook_updates: {},
            audiobook_updates: { author: 'L. E. Modesitt Jr' },
        }))
        expect(ignoreMetadataDiscrepanciesMock).toHaveBeenCalledWith(7, ['publisher'])
        await waitFor(() => expect(onComplete).toHaveBeenCalled())
    })

    it('Ignore skips every field on the pair', async () => {
        const onComplete = vi.fn()
        render(<MetadataCleanupModal onClose={vi.fn()} onComplete={onComplete} />)
        await screen.findByText('Resolving Pair 1 of 1')

        fireEvent.click(screen.getByRole('button', { name: 'Ignore' }))
        await waitFor(() => expect(ignoreMetadataDiscrepanciesMock)
            .toHaveBeenCalledWith(7, ['author', 'publisher']))
        expect(resolveMetadataDiscrepanciesMock).not.toHaveBeenCalled()
    })

    it('Unpair deletes the pair and finishes when it was the last one', async () => {
        const onComplete = vi.fn()
        render(<MetadataCleanupModal onClose={vi.fn()} onComplete={onComplete} />)
        await screen.findByText('Resolving Pair 1 of 1')

        fireEvent.click(screen.getByRole('button', { name: '✂️ Unpair' }))
        await waitFor(() => expect(deletePairMock).toHaveBeenCalledWith(7))
        await waitFor(() => expect(onComplete).toHaveBeenCalled())
    })

    it('the ✕ button closes', async () => {
        const onClose = vi.fn()
        render(<MetadataCleanupModal onClose={onClose} onComplete={vi.fn()} />)
        await screen.findByText('Resolving Pair 1 of 1')
        fireEvent.click(screen.getByRole('button', { name: '✕' }))
        expect(onClose).toHaveBeenCalled()
    })
})
