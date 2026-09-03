import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act, within } from '@testing-library/react'
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom'
import LibraryPage from './LibraryPage'
import { AuthProvider } from '../contexts/AuthContext'

// Issue #120: LibraryPage is server-driven. It renders one page of
// /api/library/items, loads more as the sentinel scrolls in (or via "Show
// more"), and every tab / sub-filter / pill / sort / search is a query param
// that becomes part of the server request — nothing is filtered in memory.

const {
    getLibraryItemsPageMock, getLibraryFacetsMock, getAllProgressMock, coverSrcMock,
    acknowledgeNewItemsMock, acknowledgeNewPairsMock, updateEbookMetadataMock,
} = vi.hoisted(() => ({
    getLibraryItemsPageMock: vi.fn(),
    getLibraryFacetsMock: vi.fn(),
    getAllProgressMock: vi.fn(),
    coverSrcMock: vi.fn(async (p) => p),
    acknowledgeNewItemsMock: vi.fn(),
    acknowledgeNewPairsMock: vi.fn(),
    updateEbookMetadataMock: vi.fn(),
}))

vi.mock('../api', async () => {
    // Real fetchAllPages (pure) — it drives "Acknowledge All" through the mock.
    const fetchAllPages = async (fetchOnePage) => {
        const all = []
        for (let page = 1; ; page++) {
            const body = await fetchOnePage(page)
            const items = body?.items || []
            all.push(...items)
            const limit = body?.limit || items.length || 1
            if (items.length < limit) break
            if (typeof body?.total === 'number' && all.length >= body.total) break
        }
        return all
    }
    return {
        getLibraryItemsPage: getLibraryItemsPageMock,
        getLibraryFacets: getLibraryFacetsMock,
        getAllProgress: getAllProgressMock,
        fetchAllPages,
        coverSrc: coverSrcMock,
        acknowledgeNewItems: acknowledgeNewItemsMock,
        acknowledgeNewPairs: acknowledgeNewPairsMock,
        updateEbookMetadata: updateEbookMetadataMock,
        updateAudiobookMetadata: vi.fn(),
        uploadEbook: vi.fn(), uploadAudiobook: vi.fn(), scanLibrary: vi.fn(), normalizeLibrary: vi.fn(),
        rescanAllLibrary: vi.fn(), deleteEbook: vi.fn(), deleteAudiobook: vi.fn(), verifyFiles: vi.fn(),
        cleanupOrphans: vi.fn(),
    }
})
// Heavy modals aren't under test.
vi.mock('../components/EnhancedMetadataModal', () => ({ default: () => <div data-testid="edit-modal" /> }))
vi.mock('../components/BulkMatchModal', () => ({ default: () => null }))
vi.mock('../components/MetadataCleanupModal', () => ({ default: () => null }))

// Mobile vs desktop is driven by useIsMobile; a per-test flag keeps the
// desktop tests below unchanged while the mobile ones (issue #213) flip it.
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
const pairItem = (pid, eid, aid) => ({
    kind: 'pair', ebook: null, audiobook: null,
    pair: { id: pid, ebook: ebook(eid), audiobook: audiobook(aid), status: 'synced', acknowledged: true },
})
const ebookItem = (id, extra) => ({ kind: 'ebook', ebook: ebook(id, extra), pair: null, audiobook: null })
const audioItem = (id, extra) => ({ kind: 'audiobook', audiobook: audiobook(id, extra), pair: null, ebook: null })

const FACETS = {
    authors: [{ name: 'Author A', count: 3 }, { name: 'Author B', count: 1 }],
    series: [{ name: 'Liveship', count: 2 }],
    counts: { ebooks: 4, audiobooks: 2, pairs: 1, unpaired: 4, new_ebooks: 1, new_audiobooks: 0, new_pairs: 1 },
}

function pageOf(items, total, page = 1, limit = 50) {
    return { items, total, page, limit }
}

function LocationProbe() {
    const loc = useLocation()
    return <div data-testid="location">{loc.pathname + loc.search}</div>
}

function renderPage(url = '/library', tab) {
    return render(
        <AuthProvider user={{ role: 'admin' }}>
            <MemoryRouter initialEntries={[url]}>
                <Routes>
                    <Route path="/library" element={<><LibraryPage tab={tab} /><LocationProbe /></>} />
                    <Route path="/library/ebooks" element={<><LibraryPage tab="ebooks" /><LocationProbe /></>} />
                    <Route path="*" element={<LocationProbe />} />
                </Routes>
            </MemoryRouter>
        </AuthProvider>,
    )
}

// The last query the page asked the server for.
const lastQuery = () => getLibraryItemsPageMock.mock.calls.at(-1)?.[0]

let observers
beforeEach(() => {
    getLibraryItemsPageMock.mockReset()
    getLibraryFacetsMock.mockReset().mockResolvedValue(FACETS)
    getAllProgressMock.mockReset().mockResolvedValue([])
    acknowledgeNewItemsMock.mockReset().mockResolvedValue({})
    acknowledgeNewPairsMock.mockReset().mockResolvedValue({})
    isMobileMock.mockReset().mockReturnValue(false)
    observers = []
    // jsdom has no IntersectionObserver; capture the callback so a test can
    // "scroll the sentinel into view".
    vi.stubGlobal('IntersectionObserver', vi.fn(function (cb) {
        this.observe = vi.fn()
        this.disconnect = vi.fn()
        this.cb = cb
        observers.push(this)
    }))
})
afterEach(() => vi.unstubAllGlobals())

describe('LibraryPage server-driven browse (issue #120)', () => {
    it('renders page 1 from /library/items and the tab counts from /library/facets', async () => {
        getLibraryItemsPageMock.mockResolvedValue(pageOf([pairItem(7, 1, 2), ebookItem(3), audioItem(4)], 3))
        renderPage()

        expect(await screen.findByText('Ebook 1')).toBeInTheDocument()   // the pair, ebook-primary
        expect(screen.getByText('Ebook 3')).toBeInTheDocument()
        expect(screen.getByText('Audio 4')).toBeInTheDocument()

        expect(getLibraryItemsPageMock).toHaveBeenCalledWith(
            expect.objectContaining({ tab: 'all', sort: 'title', dir: 'asc', page: 1, limit: 50 }))
        expect(getLibraryFacetsMock).toHaveBeenCalledWith({ tab: 'all', kind: '' })
        // Pill counts and the stats bar come from facets, not from the loaded page.
        expect(screen.getByText('All (5)')).toBeInTheDocument()          // pairs + unpaired
        expect(screen.getByText('Ebooks (4)')).toBeInTheDocument()
        expect(screen.getByText('New (2)')).toBeInTheDocument()
        expect(screen.getByText('Showing', { exact: false }).textContent).toMatch(/Showing\s*3\s*of\s*3/)
    })

    it('a tab click becomes ?tab= and a new server query; the route prop is the default tab', async () => {
        getLibraryItemsPageMock.mockResolvedValue(pageOf([ebookItem(3)], 1))
        renderPage('/library/ebooks')
        await screen.findByText('Ebook 3')
        expect(lastQuery()).toEqual(expect.objectContaining({ tab: 'ebooks' }))

        fireEvent.click(screen.getByText('Unpaired (4)'))
        await waitFor(() => expect(lastQuery().tab).toBe('unpaired'))
        expect(lastQuery().kind).toBeUndefined()   // empty filters are not sent
        expect(screen.getByTestId('location').textContent).toContain('tab=unpaired')

        // Sub-filter → kind, cleared again by a tab change.
        fireEvent.click(screen.getByText('Audiobooks', { selector: '.library-filter-pill-sub' }))
        await waitFor(() => expect(lastQuery()).toEqual(expect.objectContaining({ tab: 'unpaired', kind: 'audiobook' })))
        expect(getLibraryFacetsMock).toHaveBeenLastCalledWith({ tab: 'unpaired', kind: 'audiobook' })
        fireEvent.click(screen.getByText('Paired (1)'))
        await waitFor(() => expect(lastQuery().tab).toBe('paired'))
        expect(lastQuery().kind).toBeUndefined()
        expect(screen.getByTestId('location').textContent).not.toContain('kind=')
    })

    it('author pill options come from facets and choosing one filters server-side via ?author=', async () => {
        getLibraryItemsPageMock.mockResolvedValue(pageOf([ebookItem(3)], 1))
        renderPage()
        await screen.findByText('Ebook 3')

        fireEvent.click(screen.getByText('Author'))
        fireEvent.click(screen.getByText('Author B'))

        await waitFor(() => expect(lastQuery()).toEqual(expect.objectContaining({ author: 'Author B' })))
        expect(screen.getByTestId('location').textContent).toContain('author=Author+B')
    })

    it('?search= from the global search bar reaches the server as q (debounced) and survives reload', async () => {
        getLibraryItemsPageMock.mockResolvedValue(pageOf([ebookItem(3)], 1))
        renderPage('/library?search=dune&sort=date&dir=desc&tab=audiobooks')
        await screen.findByText('Ebook 3')

        await waitFor(() => expect(lastQuery()).toEqual(
            expect.objectContaining({ q: 'dune', sort: 'date', dir: 'desc', tab: 'audiobooks' })))
        expect(screen.getByText('dune')).toBeInTheDocument()   // the active-search chip
    })

    it('sort choice becomes ?sort=/?dir= and refetches', async () => {
        getLibraryItemsPageMock.mockResolvedValue(pageOf([ebookItem(3)], 1))
        renderPage()
        await screen.findByText('Ebook 3')

        fireEvent.click(screen.getByText(/^Sort:/))
        fireEvent.click(screen.getByText('Author', { selector: '.series-sort-option' }))
        await waitFor(() => expect(lastQuery()).toEqual(expect.objectContaining({ sort: 'author' })))

        // (the desktop sort dropdown stays open after a field pick)
        fireEvent.click(screen.getByText('↓ Descending'))
        await waitFor(() => expect(lastQuery()).toEqual(expect.objectContaining({ sort: 'author', dir: 'desc' })))
        expect(screen.getByTestId('location').textContent).toContain('sort=author&dir=desc')
    })

    it('"Show more" and the scroll sentinel each fetch the next page and append it', async () => {
        getLibraryItemsPageMock
            .mockResolvedValueOnce(pageOf([ebookItem(1), ebookItem(2)], 5, 1, 50))
            .mockResolvedValueOnce(pageOf([ebookItem(3), ebookItem(4)], 5, 2, 50))
            .mockResolvedValueOnce(pageOf([ebookItem(5)], 5, 3, 50))
        renderPage()
        await screen.findByText('Ebook 2')

        fireEvent.click(screen.getByText(/Show more \(3 remaining\)/))
        expect(await screen.findByText('Ebook 4')).toBeInTheDocument()
        expect(getLibraryItemsPageMock).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 }))
        expect(screen.getByText('Ebook 1')).toBeInTheDocument()     // appended, not replaced

        // Sentinel scrolls into view → page 3.
        await act(async () => {
            observers.at(-1).cb([{ isIntersecting: true }])
        })
        expect(await screen.findByText('Ebook 5')).toBeInTheDocument()
        expect(getLibraryItemsPageMock).toHaveBeenLastCalledWith(expect.objectContaining({ page: 3 }))
        // All 5 loaded: no more sentinel / button.
        expect(screen.queryByText(/Show more/)).not.toBeInTheDocument()
    })

    it('"Acknowledge All" on the New tab covers every new item on the server, not just the loaded page', async () => {
        getLibraryItemsPageMock.mockImplementation(async ({ tab, kind, page, limit }) => {
            if (tab === 'new' && kind === 'pair') return pageOf([pairItem(9, 90, 91)], 1, page, limit)
            if (tab === 'new') return pageOf(
                page === 1 ? [ebookItem(3, { acknowledged: false })] : [], 1, page, limit)
            return pageOf([], 0, page, limit)
        })
        renderPage('/library?tab=new')
        await screen.findByText('Ebook 3')

        fireEvent.click(screen.getByText('Acknowledge All'))

        await waitFor(() => expect(acknowledgeNewItemsMock).toHaveBeenCalledWith([3], []))
        expect(acknowledgeNewPairsMock).toHaveBeenCalledWith([9])
    })

    it('shows the empty state for a filter with no matches, and the onboarding one for an empty library', async () => {
        getLibraryItemsPageMock.mockResolvedValue(pageOf([], 0))
        renderPage('/library?search=zzz')
        expect(await screen.findByText('No books match your filters')).toBeInTheDocument()

        getLibraryFacetsMock.mockResolvedValue({ ...FACETS, counts: { ...FACETS.counts, ebooks: 0, audiobooks: 0 } })
        renderPage()
        expect(await screen.findByText('No books yet')).toBeInTheDocument()
    })
})

// Issue #213: on mobile the only search control is the box on the page, so it
// must be the same URL-backed control the desktop global bar is. A `?search=`
// in the URL (shared link, or a rotation past the 768px breakpoint) used to
// leave the box empty, inert and impossible to clear.
describe('LibraryPage mobile search is URL-backed (issue #213)', () => {
    beforeEach(() => {
        isMobileMock.mockReturnValue(true)
        getLibraryItemsPageMock.mockResolvedValue(pageOf([ebookItem(3)], 1))
    })

    const mobileInput = () => screen.getByLabelText('Search books')

    it('seeds the mobile input from ?search= in the URL', async () => {
        renderPage('/library?search=dune')
        await screen.findByText('Ebook 3')

        expect(mobileInput()).toHaveValue('dune')
        await waitFor(() => expect(lastQuery()).toEqual(expect.objectContaining({ q: 'dune' })))
    })

    it('typing in the mobile input writes ?search= and reaches the server as q', async () => {
        renderPage('/library?search=dune')
        await screen.findByText('Ebook 3')

        fireEvent.change(mobileInput(), { target: { value: 'frank' } })

        expect(mobileInput()).toHaveValue('frank')
        expect(screen.getByTestId('location').textContent).toContain('search=frank')
        await waitFor(() => expect(lastQuery()).toEqual(expect.objectContaining({ q: 'frank' })))
    })

    it('the clear button drops ?search= and the next query has no q', async () => {
        renderPage('/library?search=dune&tab=ebooks')
        await screen.findByText('Ebook 3')
        await waitFor(() => expect(lastQuery()).toEqual(expect.objectContaining({ q: 'dune' })))

        fireEvent.click(screen.getByLabelText('Clear search'))

        expect(mobileInput()).toHaveValue('')
        expect(screen.getByTestId('location').textContent).not.toContain('search=')
        expect(screen.getByTestId('location').textContent).toContain('tab=ebooks')
        await waitFor(() => expect(lastQuery().q).toBeUndefined())
    })

    it('clearing the input itself also drops ?search=', async () => {
        renderPage('/library?search=dune')
        await screen.findByText('Ebook 3')

        fireEvent.change(mobileInput(), { target: { value: '' } })

        expect(screen.getByTestId('location').textContent).not.toContain('search=')
        await waitFor(() => expect(lastQuery().q).toBeUndefined())
    })
})
