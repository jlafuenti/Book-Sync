import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
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
