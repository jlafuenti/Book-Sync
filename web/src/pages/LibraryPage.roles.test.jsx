import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import LibraryPage from './LibraryPage'
import { AuthProvider } from '../contexts/AuthContext'

/**
 * Issue #263: `GET /api/library/verify` returns the absolute file_path of every
 * orphaned row, so it is editor-gated on the server. The Maintenance menu that
 * holds "Verify Files" is already behind `hasMinRole('editor')` — this pins it,
 * because losing that gate would put a reader one click away from a 403 and,
 * before the server fix, from the operator's filesystem layout.
 */

const {
    getLibraryItemsPageMock, getLibraryFacetsMock, getAllProgressMock, verifyFilesMock,
} = vi.hoisted(() => ({
    getLibraryItemsPageMock: vi.fn(),
    getLibraryFacetsMock: vi.fn(),
    getAllProgressMock: vi.fn(),
    verifyFilesMock: vi.fn(),
}))

vi.mock('../api', () => ({
    getLibraryItemsPage: getLibraryItemsPageMock,
    getLibraryFacets: getLibraryFacetsMock,
    getAllProgress: getAllProgressMock,
    fetchAllPages: async () => [],
    coverSrc: vi.fn(async (p) => p),
    acknowledgeNewItems: vi.fn(),
    acknowledgeNewPairs: vi.fn(),
    updateEbookMetadata: vi.fn(),
    updateAudiobookMetadata: vi.fn(),
    uploadEbook: vi.fn(), uploadAudiobook: vi.fn(), scanLibrary: vi.fn(), normalizeLibrary: vi.fn(),
    rescanAllLibrary: vi.fn(), deleteEbook: vi.fn(), deleteAudiobook: vi.fn(),
    verifyFiles: verifyFilesMock,
    cleanupOrphans: vi.fn(),
}))
vi.mock('../components/EnhancedMetadataModal', () => ({ default: () => null }))
vi.mock('../components/BulkMatchModal', () => ({ default: () => null }))
vi.mock('../components/MetadataCleanupModal', () => ({ default: () => null }))
vi.mock('../hooks/useIsMobile', () => ({ default: () => false }))

const FACETS = {
    authors: [], series: [],
    counts: { ebooks: 0, audiobooks: 0, pairs: 0, unpaired: 0, new_ebooks: 0, new_audiobooks: 0, new_pairs: 0 },
}

function renderAs(role) {
    return render(
        <AuthProvider user={{ role }}>
            <MemoryRouter initialEntries={['/library']}>
                <Routes>
                    <Route path="/library" element={<LibraryPage />} />
                </Routes>
            </MemoryRouter>
        </AuthProvider>,
    )
}

beforeEach(() => {
    getLibraryItemsPageMock.mockReset().mockResolvedValue({ items: [], total: 0, page: 1, limit: 50 })
    getLibraryFacetsMock.mockReset().mockResolvedValue(FACETS)
    getAllProgressMock.mockReset().mockResolvedValue([])
    verifyFilesMock.mockReset().mockResolvedValue({ orphaned_ebooks: [], orphaned_audiobooks: [] })
    vi.stubGlobal('IntersectionObserver', vi.fn(function () {
        this.observe = vi.fn()
        this.disconnect = vi.fn()
    }))
})
afterEach(() => vi.unstubAllGlobals())

describe('LibraryPage Verify control is editor-gated (issue #263)', () => {
    it('offers no Maintenance menu, and so no Verify, to a plain user', async () => {
        renderAs('user')
        await waitFor(() => expect(getLibraryFacetsMock).toHaveBeenCalled())

        expect(screen.queryByText('Maintenance')).not.toBeInTheDocument()
        expect(screen.queryByText('Verify Files')).not.toBeInTheDocument()
    })

    it('shows Verify Files to an editor and calls the endpoint', async () => {
        renderAs('editor')
        await waitFor(() => expect(getLibraryFacetsMock).toHaveBeenCalled())

        fireEvent.click(screen.getByText('Maintenance'))
        fireEvent.click(screen.getByText('Verify Files'))

        await waitFor(() => expect(verifyFilesMock).toHaveBeenCalled())
    })

    it('never calls verifyFiles on behalf of a plain user', async () => {
        renderAs('user')
        await waitFor(() => expect(getLibraryFacetsMock).toHaveBeenCalled())

        expect(verifyFilesMock).not.toHaveBeenCalled()
    })
})
