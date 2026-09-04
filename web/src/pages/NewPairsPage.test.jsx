/**
 * Issue #276: this page's loading / error / load() triple is the shared
 * `useListFetch` hook now. It fetches two endpoints in one round trip and
 * keys the discrepancies by pair id — that shaping stays the page's job, and
 * this is what pins it.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import NewPairsPage from './NewPairsPage'

const { getNewPairsMock, getMetadataDiscrepanciesMock, acknowledgeNewPairsMock } = vi.hoisted(() => ({
    getNewPairsMock: vi.fn(),
    getMetadataDiscrepanciesMock: vi.fn(),
    acknowledgeNewPairsMock: vi.fn(),
}))

vi.mock('../api', () => ({
    getNewPairs: getNewPairsMock,
    getMetadataDiscrepancies: getMetadataDiscrepanciesMock,
    acknowledgeNewPairs: acknowledgeNewPairsMock,
    resolveMetadataDiscrepancies: vi.fn(),
    ignoreMetadataDiscrepancies: vi.fn(),
    coverSrc: vi.fn(async (p) => p),
}))

vi.mock('../components/MetadataCleanupModal', () => ({ default: () => null }))
vi.mock('../components/CoverImg', () => ({ default: () => null }))
vi.mock('../contexts/AuthContext', () => ({ useAuth: () => ({ hasMinRole: () => true }) }))

const CLEAN_PAIR = {
    id: 1, matched_at: '2026-01-01T00:00:00',
    ebook: { id: 10, title: 'Ship of Magic', filename: 'ship.epub' },
    audiobook: { id: 20, title: 'Ship of Magic', filename: 'ship.m4b' },
}
const MISMATCHED_PAIR = {
    id: 2, matched_at: '2026-01-02T00:00:00',
    ebook: { id: 11, title: 'The Mad Ship', filename: 'mad.epub' },
    audiobook: { id: 21, title: 'The Mad Ship', filename: 'mad.m4b' },
}

beforeEach(() => {
    getNewPairsMock.mockReset().mockResolvedValue([CLEAN_PAIR, MISMATCHED_PAIR])
    getMetadataDiscrepanciesMock.mockReset().mockResolvedValue([
        { pair_id: 2, title: 'The Mad Ship', discrepancies: [{ field: 'author', ebook_value: 'Hobb', audiobook_value: 'R. Hobb' }] },
    ])
    acknowledgeNewPairsMock.mockReset().mockResolvedValue({})
})

describe('NewPairsPage list fetch (issue #276)', () => {
    it('shows the loading line, then both pairs with the discrepancies keyed by pair id', async () => {
        render(<NewPairsPage />)
        expect(screen.getByText('Loading new pairs…')).toBeInTheDocument()

        expect(await screen.findByText('Ship of Magic')).toBeInTheDocument()
        expect(screen.getByText('The Mad Ship')).toBeInTheDocument()
        // One of the two carries a mismatch — the filter tabs count them.
        expect(screen.getByRole('button', { name: /Has mismatches \(1\)/ })).toBeInTheDocument()
        expect(screen.getByRole('button', { name: /No mismatches \(1\)/ })).toBeInTheDocument()
    })

    it('shows the empty state when nothing is waiting', async () => {
        getNewPairsMock.mockResolvedValue([])
        getMetadataDiscrepanciesMock.mockResolvedValue([])
        render(<NewPairsPage />)

        expect(await screen.findByText(/No new pairs/)).toBeInTheDocument()
    })

    it('renders a failure from either endpoint instead of an empty page', async () => {
        getMetadataDiscrepanciesMock.mockRejectedValue(new Error('discrepancies are down'))
        render(<NewPairsPage />)

        expect(await screen.findByText('discrepancies are down')).toBeInTheDocument()
    })

    it('reloads after acknowledging the clean pairs', async () => {
        render(<NewPairsPage />)
        await screen.findByText('Ship of Magic')

        fireEvent.click(screen.getByRole('button', { name: /No mismatches \(1\)/ }))
        fireEvent.click(screen.getByRole('button', { name: /Acknowledge all clean \(1\)/ }))

        await waitFor(() => expect(acknowledgeNewPairsMock).toHaveBeenCalledWith([1]))
        await waitFor(() => expect(getNewPairsMock).toHaveBeenCalledTimes(2))
    })
})
