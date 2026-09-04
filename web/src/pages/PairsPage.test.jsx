import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import PairsPage from './PairsPage'
import { AuthProvider } from '../contexts/AuthContext'

const { getPairsMock, getUnpairedMediaMock, getAllProgressMock } = vi.hoisted(() => ({
    getPairsMock: vi.fn(),
    getUnpairedMediaMock: vi.fn(),
    getAllProgressMock: vi.fn(),
}))

vi.mock('../api', () => ({
    getPairs: getPairsMock,
    getUnpairedMedia: getUnpairedMediaMock,
    getAllProgress: getAllProgressMock,
    createPair: vi.fn(),
    deletePair: vi.fn(),
    coverSrc: vi.fn(async (p) => p),
}))
vi.mock('../components/MetadataCleanupModal', () => ({ default: () => null }))

beforeEach(() => {
    getPairsMock.mockReset().mockResolvedValue([])
    getAllProgressMock.mockReset().mockResolvedValue([])
    getUnpairedMediaMock.mockReset().mockResolvedValue({
        ebooks: [{ id: 1, title: 'Lonely Ebook', author: 'A', format: 'epub' }],
        audiobooks: [{ id: 2, title: 'Lonely Audio', author: 'B', format: 'm4b' }],
    })
})

function renderTab(tab) {
    return render(
        <AuthProvider user={{ role: 'admin' }}>
            <MemoryRouter><PairsPage tab={tab} /></MemoryRouter>
        </AuthProvider>,
    )
}

// Issue #120: the manual-pairing pickers are fed by the bounded
// `getUnpairedMedia()` helper (tab=unpaired on /library/items); the page no
// longer walks every ebook and audiobook to set-diff them against the pairs.
describe('PairsPage unpaired tabs', () => {
    it('lists unpaired ebooks from getUnpairedMedia()', async () => {
        renderTab('unpaired-books')
        expect(await screen.findByText('Lonely Ebook')).toBeInTheDocument()
        expect(getUnpairedMediaMock).toHaveBeenCalledTimes(1)
    })

    it('lists unpaired audiobooks from getUnpairedMedia()', async () => {
        renderTab('unpaired-audiobooks')
        expect(await screen.findByText('Lonely Audio')).toBeInTheDocument()
    })
})

// Issue #215: the pair table's title link is the third consumer of the
// routing rule, and it used to compare two always-equal `updated_at` stamps
// like Home and the Library did.
describe('PairsPage pair link routes on bookmarks.source', () => {
    const sameInstant = '2026-03-01T00:00:00Z'

    function setupPair(source) {
        getPairsMock.mockResolvedValue([{
            id: 7, status: 'synced',
            ebook: { id: 10, title: 'Ship of Magic', author: 'Robin Hobb', format: 'epub' },
            audiobook: { id: 20, title: 'Ship of Magic', author: 'Robin Hobb', format: 'm4b' },
        }])
        getAllProgressMock.mockResolvedValue([
            { media_type: 'ebook', ebook_id: 10, book_pair_id: 7, source, updated_at: sameInstant },
            { media_type: 'audiobook', audiobook_id: 20, book_pair_id: 7, source, updated_at: sameInstant },
        ])
    }

    it('links to the audiobook when the pair`s source claims it', async () => {
        setupPair('audiobook')
        renderTab('paired')
        expect(await screen.findByText('Ship of Magic'))
            .toHaveAttribute('href', '/book/audiobook/20')
    })

    it('links to the ebook when the source claims the ebook', async () => {
        setupPair('ebook')
        renderTab('paired')
        expect(await screen.findByText('Ship of Magic'))
            .toHaveAttribute('href', '/book/ebook/10')
    })
})

// Issue #279: the pairing modal is now the shared Modal primitive.
describe('PairsPage pairing modal is a real dialog', () => {
    it('opens labelled by its heading and closes on Escape, restoring focus', async () => {
        renderTab('unpaired-books')
        const trigger = await screen.findByRole('button', { name: '🎧 Pair' })

        trigger.focus()
        fireEvent.click(trigger)

        const dialog = screen.getByRole('dialog')
        expect(dialog).toHaveAttribute('aria-modal', 'true')
        expect(dialog).toHaveAccessibleName('Pair "Lonely Ebook" with an Audiobook')
        expect(dialog.contains(document.activeElement)).toBe(true)
        expect(screen.getByRole('button', { name: 'Create Pair' })).toBeDisabled()

        fireEvent.keyDown(document, { key: 'Escape' })
        expect(screen.queryByRole('dialog')).toBeNull()
        expect(document.activeElement).toBe(trigger)
    })
})
