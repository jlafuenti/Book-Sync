/**
 * Issue #276: this page's loading / error / load() triple is the shared
 * `useListFetch` hook now. Its three states — spinner line, error line, empty
 * state — and its reload-after-acknowledge had no test at all before.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import NewItemsPage from './NewItemsPage'

const { getNewItemsMock, acknowledgeNewItemsMock } = vi.hoisted(() => ({
    getNewItemsMock: vi.fn(),
    acknowledgeNewItemsMock: vi.fn(),
}))

vi.mock('../api', () => ({
    getNewItems: getNewItemsMock,
    acknowledgeNewItems: acknowledgeNewItemsMock,
    uploadEbookCover: vi.fn(),
    uploadAudiobookCover: vi.fn(),
    updateEbookMetadata: vi.fn(),
    updateAudiobookMetadata: vi.fn(),
}))

vi.mock('../components/EnhancedMetadataModal', () => ({ default: () => null }))
vi.mock('../contexts/AuthContext', () => ({ useAuth: () => ({ hasMinRole: () => true }) }))

const EBOOK = {
    id: 11, title: 'Ship of Magic', author: 'Robin Hobb', series: null,
    format: 'epub', file_size: 2048, uploaded_at: '2026-01-01T00:00:00',
}
const AUDIO = {
    id: 22, title: 'The Mad Ship', author: 'Robin Hobb', series: null,
    format: 'm4b', file_size: 4096, uploaded_at: '2026-01-02T00:00:00',
}

beforeEach(() => {
    getNewItemsMock.mockReset().mockResolvedValue({ ebooks: [EBOOK], audiobooks: [AUDIO] })
    acknowledgeNewItemsMock.mockReset().mockResolvedValue({})
})

describe('NewItemsPage list fetch (issue #276)', () => {
    it('shows the loading line, then the two lists', async () => {
        render(<NewItemsPage />)
        expect(screen.getByText('Loading new items…')).toBeInTheDocument()

        expect(await screen.findByText('Ship of Magic')).toBeInTheDocument()
        expect(screen.getByText('The Mad Ship')).toBeInTheDocument()
    })

    it('shows the empty state when nothing is waiting', async () => {
        getNewItemsMock.mockResolvedValue({ ebooks: [], audiobooks: [] })
        render(<NewItemsPage />)

        expect(await screen.findByText(/No new items/)).toBeInTheDocument()
    })

    it('renders the failure instead of an empty page', async () => {
        getNewItemsMock.mockRejectedValue(new Error('server said no'))
        render(<NewItemsPage />)

        expect(await screen.findByText('server said no')).toBeInTheDocument()
    })

    it('reloads after acknowledging everything', async () => {
        render(<NewItemsPage />)
        await screen.findByText('Ship of Magic')

        fireEvent.click(screen.getByRole('button', { name: 'Acknowledge all' }))

        await waitFor(() => expect(acknowledgeNewItemsMock).toHaveBeenCalledWith([11], [22]))
        await waitFor(() => expect(getNewItemsMock).toHaveBeenCalledTimes(2))
    })
})
