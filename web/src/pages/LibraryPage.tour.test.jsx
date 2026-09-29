import { useState, useMemo } from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom'
import LibraryPage from './LibraryPage'
import { AuthProvider } from '../contexts/AuthContext'
import { TourAnchors, TourScreens, TourEvents, TourRegistryContext, TourControllerContext } from '../tour/anchors'
import { TourAnchorRegistry } from '../tour/TourAnchorRegistry'
import { TourStateContext } from '../tour/TourContext'

// Issue #598 Track B: LibraryPage wires the walkthrough's filter/sort/
// maintenance anchors onto real controls, adopts a real on-screen synced pair
// for `library_open_book` to spotlight (mirroring Android's "adopt the first
// synced pair in the Library's own ordering", issue #652), and reports
// `detailsOpened` when a card is opened.

const {
    getLibraryItemsPageMock, getLibraryFacetsMock, getAllProgressMock,
} = vi.hoisted(() => ({
    getLibraryItemsPageMock: vi.fn(),
    getLibraryFacetsMock: vi.fn(),
    getAllProgressMock: vi.fn(),
}))

vi.mock('../api', () => ({
    getLibraryItemsPage: getLibraryItemsPageMock,
    getLibraryFacets: getLibraryFacetsMock,
    getAllProgress: getAllProgressMock,
    fetchAllPages: async () => [],
    coverSrc: vi.fn(async (p) => p),
    acknowledgeNewItems: vi.fn(), acknowledgeNewPairs: vi.fn(),
    updateEbookMetadata: vi.fn(), updateAudiobookMetadata: vi.fn(),
    uploadEbook: vi.fn(), uploadAudiobook: vi.fn(), scanLibrary: vi.fn(), normalizeLibrary: vi.fn(),
    rescanAllLibrary: vi.fn(), deleteEbook: vi.fn(), deleteAudiobook: vi.fn(), verifyFiles: vi.fn(),
    cleanupOrphans: vi.fn(),
}))
vi.mock('../components/EnhancedMetadataModal', () => ({ default: () => null }))
vi.mock('../components/BulkMatchModal', () => ({ default: () => null }))
vi.mock('../components/MetadataCleanupModal', () => ({ default: () => null }))

const { isMobileMock } = vi.hoisted(() => ({ isMobileMock: vi.fn(() => false) }))
vi.mock('../hooks/useIsMobile', () => ({ default: () => isMobileMock() }))

const ebook = (id, extra = {}) => ({
    id, title: `Ebook ${id}`, author: 'Author A', series: null, series_index: null, format: 'epub',
    cover_path: null, uploaded_at: '2026-01-01T00:00:00Z', file_size: 100, acknowledged: true, ...extra,
})
const audiobook = (id, extra = {}) => ({
    id, title: `Audio ${id}`, author: 'Author B', series: null, series_index: null, format: 'm4b',
    cover_path: null, uploaded_at: '2026-01-01T00:00:00Z', file_size: 1000, acknowledged: true, ...extra,
})
const pairItem = (pid, eid, aid, status = 'synced') => ({
    kind: 'pair', ebook: null, audiobook: null,
    pair: { id: pid, ebook: ebook(eid), audiobook: audiobook(aid), status, acknowledged: true },
})

const FACETS = {
    authors: [], series: [],
    counts: { ebooks: 0, audiobooks: 0, pairs: 2, unpaired: 0, new_ebooks: 0, new_audiobooks: 0, new_pairs: 0 },
}

function pageOf(items, total, page = 1, limit = 50) {
    return { items, total, page, limit }
}

// A minimal stand-in for TourProvider's context value: real enough that
// LibraryPage's `useTour()` (state.status/pairId + adoptPair) behaves like
// the real thing, without pulling in the whole engine.
function FakeTour({ running = true, registry, children }) {
    const [pairId, setPairId] = useState(null)
    const value = useMemo(() => ({
        state: { status: running ? 'running' : 'idle', pairId, step: null, index: -1, total: 0, resolution: 'pending', willCleanUp: false, canGoBack: false },
        start: () => {}, next: () => {}, back: () => {}, quit: () => {}, skip: () => {}, replay: () => {},
        // Mirrors the real TourController.adoptPair (TourController.js): it
        // sets both its own state and the registry's wantedPairId, which is
        // what actually gates a LibraryPairCard's anchor registration.
        adoptPair: (pair) => {
            const id = pair ? pair.id : null
            setPairId(id)
            registry?.setWantedPairId(id)
        },
    }), [running, pairId, registry])
    return <TourStateContext.Provider value={value}>{children}</TourStateContext.Provider>
}

function LocationProbe({ onLocation }) {
    onLocation(useLocation())
    return <div>book-detail-stub</div>
}

function renderPage({ role = 'admin', registry, tourController, tourRunning = true } = {}) {
    let tree = (
        <AuthProvider user={{ role }}>
            <MemoryRouter initialEntries={['/library']}>
                <Routes>
                    <Route path="/library" element={<LibraryPage />} />
                    <Route path="/book/:type/:id" element={<div>book-detail-stub</div>} />
                    <Route path="/pairs/:sub" element={<div>pairs-stub</div>} />
                </Routes>
            </MemoryRouter>
        </AuthProvider>
    )
    tree = <FakeTour running={tourRunning} registry={registry}>{tree}</FakeTour>
    if (tourController) tree = <TourControllerContext.Provider value={tourController}>{tree}</TourControllerContext.Provider>
    if (registry) tree = <TourRegistryContext.Provider value={registry}>{tree}</TourRegistryContext.Provider>
    return render(tree)
}

let observers
beforeEach(() => {
    getLibraryItemsPageMock.mockReset()
    getLibraryFacetsMock.mockReset().mockResolvedValue(FACETS)
    getAllProgressMock.mockReset().mockResolvedValue([])
    isMobileMock.mockReset().mockReturnValue(false)
    Element.prototype.scrollIntoView = vi.fn()
    observers = []
    vi.stubGlobal('IntersectionObserver', vi.fn(function (cb) {
        this.observe = vi.fn()
        this.disconnect = vi.fn()
        this.cb = cb
        observers.push(this)
    }))
})
afterEach(() => vi.unstubAllGlobals())

describe('LibraryPage tour anchors (issue #598 Track B)', () => {
    it('tags the filter pills row and the desktop sort/search group', async () => {
        getLibraryItemsPageMock.mockResolvedValue(pageOf([], 0))
        renderPage()
        await screen.findByText('No books yet')

        expect(document.querySelector('.library-filter-pills')).toHaveAttribute('data-tour', TourAnchors.LibraryFilterPills)
        expect(document.querySelector('.library-sort-search-group')).toHaveAttribute('data-tour', TourAnchors.LibrarySortSearch)
    })

    it('tags the mobile search box with the same LibrarySortSearch anchor when on mobile', async () => {
        isMobileMock.mockReturnValue(true)
        getLibraryItemsPageMock.mockResolvedValue(pageOf([], 0))
        renderPage()
        await screen.findByText('No books yet')

        expect(screen.getByLabelText('Search books').closest('.library-mobile-search'))
            .toHaveAttribute('data-tour', TourAnchors.LibrarySortSearch)
    })

    it('tags the Upload/Maintenance/Select group for an editor, and renders none of it for a plain user', async () => {
        getLibraryItemsPageMock.mockResolvedValue(pageOf([], 0))
        renderPage({ role: 'admin' })
        await screen.findByText('No books yet')
        expect(document.querySelector('.library-maintenance-group')).toHaveAttribute('data-tour', TourAnchors.LibraryMaintenance)

        renderPage({ role: 'user' })
        await screen.findAllByText('No books yet')
        expect(document.querySelectorAll('.library-maintenance-group').length).toBe(1) // only the admin render above
    })

    it('adopts the first synced pair in the Library\'s own order, spotlights and scrolls to its card, then settles', async () => {
        // Ebook 3 is an unpaired item ahead of both pairs alphabetically; pair 8
        // is unsynced; pair 9 is the first *synced* pair — that is the one that
        // must be adopted, not simply "the first item".
        getLibraryItemsPageMock.mockResolvedValue(pageOf([
            { kind: 'ebook', ebook: ebook(3), pair: null, audiobook: null },
            pairItem(8, 80, 81, 'transcribing'),
            pairItem(9, 90, 91, 'synced'),
        ], 3))
        const registry = new TourAnchorRegistry()
        renderPage({ registry })

        await screen.findByText('Ebook 90')
        await waitFor(() => expect(registry.wantedPairId).toBe(9))
        // getBoundingClientRect() is a jsdom stub that returns a zero-size
        // rect, which the registry drops (TourAnchorRegistry.set) — a real
        // browser lays the card out with real dimensions, but the gating this
        // test cares about (the correct pairId became wanted) is unaffected.
        expect(Element.prototype.scrollIntoView).toHaveBeenCalled()
        await waitFor(() => expect(registry.screenState(TourScreens.Library)).toBe('settled'))
    })

    it('does not adopt a pair, and settles immediately, when no tour is running', async () => {
        getLibraryItemsPageMock.mockResolvedValue(pageOf([pairItem(9, 90, 91, 'synced')], 1))
        const registry = new TourAnchorRegistry()
        renderPage({ registry, tourRunning: false })

        await screen.findByText('Ebook 90')
        await waitFor(() => expect(registry.screenState(TourScreens.Library)).toBe('settled'))
        // No tour running means no wanted pair, so nothing gets scrolled to,
        // and the registry never records a rect for the anchor even though
        // the synced pair's card still carries the debug attribute.
        expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled()
        expect(registry.wantedPairId).toBeNull()
    })

    it('routes the tour’s own pair card to the ebook page even when the audiobook was used last', async () => {
        // The next step says "Click Read to open the ebook": the audiobook
        // page has no Read button (seen live on 2026-09-28, where a pair
        // last used as audio opened /book/audiobook/… and the tour stalled).
        getLibraryItemsPageMock.mockResolvedValue(pageOf([pairItem(9, 90, 91, 'synced')], 1))
        getAllProgressMock.mockResolvedValue([{ book_pair_id: 9, source: 'audiobook', audio_position_ms: 40000 }])
        const registry = new TourAnchorRegistry()
        const controller = { onEvent: vi.fn() }
        let location
        render(
            <TourRegistryContext.Provider value={registry}>
                <TourControllerContext.Provider value={controller}>
                    <FakeTour registry={registry}>
                        <AuthProvider user={{ role: 'user' }}>
                            <MemoryRouter initialEntries={['/library']}>
                                <Routes>
                                    <Route path="/library" element={<LibraryPage />} />
                                    <Route path="/book/:type/:id" element={<LocationProbe onLocation={(l) => { location = l }} />} />
                                </Routes>
                            </MemoryRouter>
                        </AuthProvider>
                    </FakeTour>
                </TourControllerContext.Provider>
            </TourRegistryContext.Provider>,
        )

        await screen.findByText('Ebook 90')
        await waitFor(() => expect(registry.wantedPairId).toBe(9))
        fireEvent.click(screen.getByText('Ebook 90'))

        await waitFor(() => expect(location?.pathname).toBe('/book/ebook/90'))
        expect(controller.onEvent).toHaveBeenCalledWith(expect.objectContaining({ kind: 'detailsOpened' }))
    })

    it('emits detailsOpened when a card navigates to the book page', async () => {
        getLibraryItemsPageMock.mockResolvedValue(pageOf([{ kind: 'ebook', ebook: ebook(3), pair: null, audiobook: null }], 1))
        const controller = { onEvent: vi.fn() }
        renderPage({ tourController: controller })

        fireEvent.click(await screen.findByText('Ebook 3'))

        expect(await screen.findByText('book-detail-stub')).toBeInTheDocument()
        expect(controller.onEvent).toHaveBeenCalledWith(expect.objectContaining({ kind: 'detailsOpened' }))
    })
})
