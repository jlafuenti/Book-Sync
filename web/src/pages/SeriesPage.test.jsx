import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import SeriesPage, { SeriesCard, SeriesListRow, SeriesBookRow } from './SeriesPage'

const {
    coverSrcMock, getEbooksMock, getAudiobooksMock, getPairsMock,
    updatePositionMock, resetPairProgressMock, resetPositionMock, confirmMock, authRef,
    updateEbookMetadataMock, updateAudiobookMetadataMock,
} = vi.hoisted(() => ({
    confirmMock: vi.fn(),
    coverSrcMock: vi.fn(),
    getEbooksMock: vi.fn(),
    getAudiobooksMock: vi.fn(),
    getPairsMock: vi.fn(),
    updatePositionMock: vi.fn(),
    resetPairProgressMock: vi.fn(),
    resetPositionMock: vi.fn(),
    updateEbookMetadataMock: vi.fn(),
    updateAudiobookMetadataMock: vi.fn(),
    authRef: { role: 'editor' },
}))

vi.mock('../api', () => ({
    coverSrc: coverSrcMock,
    getEbooks: getEbooksMock,
    getAudiobooks: getAudiobooksMock,
    getPairs: getPairsMock,
    updateEbookMetadata: updateEbookMetadataMock,
    updateAudiobookMetadata: updateAudiobookMetadataMock,
    updatePosition: updatePositionMock,
    resetPairProgress: resetPairProgressMock,
    resetPosition: resetPositionMock,
    getDeviceId: () => 'dev-1',
    getDeviceName: () => 'Test Browser',
}))

const ROLE_HIERARCHY = { superadmin: 4, admin: 3, editor: 2, user: 1 }
vi.mock('../contexts/AuthContext', () => ({
    useAuth: () => ({
        hasMinRole: (min) => (ROLE_HIERARCHY[authRef.role] || 0) >= (ROLE_HIERARCHY[min] || 0),
    }),
}))

function group(overrides = {}) {
    return {
        name: 'A Series',
        author: 'An Author',
        items: [{ key: '1', type: 'ebook', hasEbook: true, hasAudiobook: false, title: 'Book 1', seriesIndex: 1 }],
        covers: ['/api/files/covers/a.jpg'],
        ...overrides,
    }
}

beforeEach(() => {
    authRef.role = 'editor'
    coverSrcMock.mockReset().mockResolvedValue('/api/files/covers/a.jpg?token=tok')
    getEbooksMock.mockReset().mockResolvedValue([])
    getAudiobooksMock.mockReset().mockResolvedValue([])
    getPairsMock.mockReset().mockResolvedValue([])
    updatePositionMock.mockReset().mockResolvedValue({})
    resetPairProgressMock.mockReset().mockResolvedValue({})
    resetPositionMock.mockReset().mockResolvedValue({})
    updateEbookMetadataMock.mockReset().mockResolvedValue({})
    updateAudiobookMetadataMock.mockReset().mockResolvedValue({})
    // Assigned rather than spied: vi.restoreAllMocks() would take the shared
    // matchMedia stub from src/test/setup.js down with it.
    confirmMock.mockReset().mockReturnValue(true)
    window.confirm = confirmMock
})

describe('SeriesPage SeriesCard', () => {
    it('renders the resolved cover image for the series stack', async () => {
        const { container } = render(
            <SeriesCard
                group={group()}
                selectMode={false}
                selectedKeys={new Set()}
                onToggleSelect={() => {}}
                onStartSelect={() => {}}
                onClick={() => {}}
                onAuthorClick={() => {}}
            />
        )
        await waitFor(() => expect(container.querySelector('img')).toHaveAttribute('src', '/api/files/covers/a.jpg?token=tok'))
    })
})

describe('SeriesPage SeriesListRow', () => {
    it('renders the resolved cover image for the row thumbnail', async () => {
        const { container } = render(
            <SeriesListRow
                group={group()}
                selectMode={false}
                selectedKeys={new Set()}
                onToggleSelect={() => {}}
                isExpanded={false}
                onToggleExpand={() => {}}
                onSeriesClick={() => {}}
                onAuthorClick={() => {}}
            />
        )
        await waitFor(() => expect(container.querySelector('img')).toHaveAttribute('src', '/api/files/covers/a.jpg?token=tok'))
    })
})

describe('SeriesPage SeriesBookRow', () => {
    it('renders the resolved cover image when the item has a cover_path', async () => {
        const { container } = render(<SeriesBookRow item={{ title: 'Book 1', type: 'ebook', cover_path: '/api/files/covers/a.jpg' }} />)
        await waitFor(() => expect(container.querySelector('img')).toHaveAttribute('src', '/api/files/covers/a.jpg?token=tok'))
    })

    it('shows a placeholder when there is no cover_path', () => {
        const { container } = render(<SeriesBookRow item={{ title: 'Book 1', type: 'ebook', cover_path: null }} />)
        expect(container.querySelector('img')).not.toBeInTheDocument()
        expect(coverSrcMock).not.toHaveBeenCalled()
    })
})


// ---------------------------------------------------------------------------
// Issue #270: Android clears a finished series in one action (CardOverflowMenu
// "Mark series complete" / "Reset series progress"); the web made you repeat it
// book by book. Implemented as a loop of the same per-book calls, scope-correct
// per member: a pair writes once at pair scope (both halves share one canonical
// record), an unpaired book writes at its own media scope.
//
// Per docs/position-sync-contract.md a completion toggle carries no anchor
// fields and no `source` — only the device attribution.
// ---------------------------------------------------------------------------

describe('SeriesPage — series-level progress actions (issue #270)', () => {
    const EBOOK_1 = {
        id: 1, title: 'Book One', author: 'An Author', series: 'A Series',
        series_index: 1, cover_path: null, uploaded_at: '2026-01-01',
    }
    const AUDIO_2 = {
        id: 2, title: 'Book One', author: 'An Author', series: 'A Series',
        series_index: 1, cover_path: null, uploaded_at: '2026-01-01',
    }
    const EBOOK_3 = {
        id: 3, title: 'Book Two', author: 'An Author', series: 'A Series',
        series_index: 2, cover_path: null, uploaded_at: '2026-01-02',
    }

    async function renderSeriesPage() {
        getEbooksMock.mockResolvedValue([EBOOK_1, EBOOK_3])
        getAudiobooksMock.mockResolvedValue([AUDIO_2])
        getPairsMock.mockResolvedValue([{ id: 5, ebook: EBOOK_1, audiobook: AUDIO_2 }])

        render(<MemoryRouter><SeriesPage /></MemoryRouter>)
        await screen.findByText('A Series')
    }

    async function selectTheSeries() {
        fireEvent.click(screen.getByRole('button', { name: 'Select' }))
        fireEvent.click(document.querySelector('.series-card-checkbox'))
        await screen.findByText(/2 selected/)
    }

    it('marks every member complete at the right scope, pair-first', async () => {
        await renderSeriesPage()
        await selectTheSeries()

        fireEvent.click(screen.getByRole('button', { name: /Mark Complete/i }))

        await waitFor(() => expect(updatePositionMock).toHaveBeenCalledTimes(2))
        const meta = { device_id: 'dev-1', device_name: 'Test Browser', captured_at: expect.any(String) }
        expect(updatePositionMock).toHaveBeenCalledWith('pair', 5, { is_completed: true, ...meta })
        expect(updatePositionMock).toHaveBeenCalledWith('ebook', 3, { is_completed: true, ...meta })

        // No anchor fields, no `source` — a completion toggle claims neither.
        for (const [, , body] of updatePositionMock.mock.calls) {
            expect(Object.keys(body).sort()).toEqual(
                ['captured_at', 'device_id', 'device_name', 'is_completed'],
            )
        }
    })

    it('resets every member at the right scope', async () => {
        await renderSeriesPage()
        await selectTheSeries()

        fireEvent.click(screen.getByRole('button', { name: /Reset Progress/i }))

        await waitFor(() => expect(resetPairProgressMock).toHaveBeenCalledWith(5))
        expect(resetPositionMock).toHaveBeenCalledWith('ebook', 3)
        expect(resetPositionMock).toHaveBeenCalledTimes(1)
        expect(updatePositionMock).not.toHaveBeenCalled()
    })

    it('names the number of books in the confirm prompt', async () => {
        await renderSeriesPage()
        await selectTheSeries()

        fireEvent.click(screen.getByRole('button', { name: /Mark Complete/i }))

        expect(confirmMock.mock.calls[0][0]).toMatch(/2 book/)
    })

    it('writes nothing when the confirm is cancelled', async () => {
        confirmMock.mockReturnValue(false)
        await renderSeriesPage()
        await selectTheSeries()

        fireEvent.click(screen.getByRole('button', { name: /Mark Complete/i }))
        fireEvent.click(screen.getByRole('button', { name: /Reset Progress/i }))

        expect(updatePositionMock).not.toHaveBeenCalled()
        expect(resetPairProgressMock).not.toHaveBeenCalled()
        expect(resetPositionMock).not.toHaveBeenCalled()
    })

    it('reloads the page data afterwards, as bulk edit does', async () => {
        await renderSeriesPage()
        await selectTheSeries()
        const before = getPairsMock.mock.calls.length

        fireEvent.click(screen.getByRole('button', { name: /Mark Complete/i }))

        await waitFor(() => expect(getPairsMock.mock.calls.length).toBeGreaterThan(before))
    })

    it('is hidden from a plain user, who cannot select in the first place', async () => {
        authRef.role = 'user'
        await renderSeriesPage()

        expect(screen.queryByRole('button', { name: 'Select' })).not.toBeInTheDocument()
    })

    it('surfaces a failure instead of failing silently', async () => {
        updatePositionMock.mockRejectedValue(new Error('server said no'))
        await renderSeriesPage()
        await selectTheSeries()

        fireEvent.click(screen.getByRole('button', { name: /Mark Complete/i }))

        expect(await screen.findByText(/server said no/)).toBeInTheDocument()
    })

    // Issue #276: bulk edit was a verbatim copy of LibraryPage's and was
    // unpinned on both pages. It is the shared BulkMetadataEditModal now, so
    // this is the test that says the page still wires it to the right ids.
    it('bulk-edits every selected member — one PATCH per ebook and audiobook id', async () => {
        await renderSeriesPage()
        await selectTheSeries()

        fireEvent.click(screen.getByRole('button', { name: 'Edit Metadata' }))

        const dialog = screen.getByRole('dialog')
        expect(dialog).toHaveAccessibleName('Edit 2 Items')

        fireEvent.change(screen.getAllByPlaceholderText('Leave blank to keep unchanged')[0], {
            target: { value: 'Robin Hobb' },
        })
        fireEvent.click(screen.getByRole('button', { name: 'Save to 2 Items' }))

        const patch = { author: 'Robin Hobb' }
        await waitFor(() => expect(updateEbookMetadataMock).toHaveBeenCalledTimes(2))
        expect(updateEbookMetadataMock).toHaveBeenCalledWith(1, patch)   // the pair's ebook
        expect(updateEbookMetadataMock).toHaveBeenCalledWith(3, patch)   // the unpaired one
        expect(updateAudiobookMetadataMock).toHaveBeenCalledWith(2, patch)
        expect(updateAudiobookMetadataMock).toHaveBeenCalledTimes(1)

        await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    })

    it('closes bulk edit on Escape without writing anything', async () => {
        await renderSeriesPage()
        await selectTheSeries()

        fireEvent.click(screen.getByRole('button', { name: 'Edit Metadata' }))
        expect(screen.getByRole('dialog')).toBeInTheDocument()

        fireEvent.keyDown(document, { key: 'Escape' })

        expect(screen.queryByRole('dialog')).toBeNull()
        expect(updateEbookMetadataMock).not.toHaveBeenCalled()
    })
})
