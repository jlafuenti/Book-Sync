import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { setViewport } from '../test/setup'
import TourOverlay from './TourOverlay'
import { TourStateContext } from './TourContext'
import { TourRegistryContext } from './anchors'
import { TourAnchorRegistry } from './TourAnchorRegistry'

function rect(overrides = {}) {
    return { top: 100, left: 100, right: 200, bottom: 150, width: 100, height: 50, x: 100, y: 100, ...overrides }
}

function renderOverlay(stateOverrides = {}, { registry } = {}) {
    const reg = registry || new TourAnchorRegistry()
    const controls = {
        start: vi.fn(), next: vi.fn(), back: vi.fn(), quit: vi.fn(), skip: vi.fn(), replay: vi.fn(),
    }
    const state = {
        status: 'running',
        step: {
            id: 'library_filters', screen: 'Library', anchor: null,
            title: 'Filters', body: 'These pills narrow the list.',
            advance: { kind: 'next' },
        },
        index: 0,
        total: 5,
        resolution: 'found',
        willCleanUp: false,
        pairId: null,
        canGoBack: false,
        ...stateOverrides,
    }
    const value = { state, ...controls }
    const result = render(
        <TourRegistryContext.Provider value={reg}>
            <TourStateContext.Provider value={value}>
                <TourOverlay />
            </TourStateContext.Provider>
        </TourRegistryContext.Provider>,
    )
    return { ...result, reg, controls }
}

describe('TourOverlay', () => {
    it('renders nothing when idle', () => {
        renderOverlay({ status: 'idle', step: null })
        expect(screen.queryByTestId('tour-overlay')).not.toBeInTheDocument()
    })

    it('renders nothing when finished', () => {
        renderOverlay({ status: 'finished' })
        expect(screen.queryByTestId('tour-overlay')).not.toBeInTheDocument()
    })

    it('renders a full-viewport blocker and no hole when the step has no anchor', () => {
        renderOverlay()
        expect(screen.getByTestId('tour-blocker-full')).toBeInTheDocument()
        expect(screen.queryByTestId('tour-hole')).not.toBeInTheDocument()
    })

    it('frames the anchor with four blockers and a hole when resolution is found', () => {
        const reg = new TourAnchorRegistry()
        reg.set('LibraryFilterPills', rect())
        renderOverlay({
            step: {
                id: 'library_filters', screen: 'Library', anchor: 'LibraryFilterPills',
                title: 'Filters', body: 'These pills narrow the list.', advance: { kind: 'next' },
            },
            resolution: 'found',
        }, { registry: reg })

        expect(screen.getByTestId('tour-hole')).toBeInTheDocument()
        expect(screen.getByTestId('tour-blocker-top')).toBeInTheDocument()
        expect(screen.getByTestId('tour-blocker-bottom')).toBeInTheDocument()
        expect(screen.getByTestId('tour-blocker-left')).toBeInTheDocument()
        expect(screen.getByTestId('tour-blocker-right')).toBeInTheDocument()
        expect(screen.queryByTestId('tour-blocker-full')).not.toBeInTheDocument()
    })

    it('shows a compact "One moment…" card while resolution is pending', () => {
        renderOverlay({ resolution: 'pending' })
        expect(screen.getByText('One moment…')).toBeInTheDocument()
        expect(screen.queryByText('These pills narrow the list.')).not.toBeInTheDocument()
    })

    it('shows the emptyBody copy when resolution is missing', () => {
        renderOverlay({
            resolution: 'missing',
            step: {
                id: 'series_groups', screen: 'Series', anchor: 'SeriesGrid',
                title: 'Series', body: 'A series card keeps its books together, in order.',
                emptyBody: 'No series yet: books group here once their metadata carries a series name.',
                advance: { kind: 'next' },
            },
        })
        expect(screen.getByText(
            'No series yet: books group here once their metadata carries a series name.',
        )).toBeInTheDocument()
    })

    it('shows the "Tap the highlighted control" hint on a tapAnchor step once found', () => {
        renderOverlay({
            step: {
                id: 'library_open_book', screen: 'Library', anchor: null,
                title: 'Open a book', body: 'Click this book to open its page.',
                advance: { kind: 'tapAnchor', event: { kind: 'detailsOpened' } },
            },
        })
        expect(screen.getByText('Tap the highlighted control')).toBeInTheDocument()
        expect(screen.queryByText('Next')).not.toBeInTheDocument()
    })

    it('shows a Next button for a plain "next" step and calls next() on click', () => {
        const { controls } = renderOverlay()
        fireEvent.click(screen.getByText('Next'))
        expect(controls.next).toHaveBeenCalledTimes(1)
    })

    it('shows a Done button on the final "finish" step', () => {
        renderOverlay({
            step: { id: 'done', screen: 'Account', anchor: null, title: 'Done', body: 'That\'s Tandem.', advance: { kind: 'finish' } },
        })
        expect(screen.getByRole('button', { name: 'Done' })).toBeInTheDocument()
        expect(screen.queryByText('Next')).not.toBeInTheDocument()
    })

    it('shows Skip only for a skippable waitFor step, and calls skip()', () => {
        const { controls } = renderOverlay({
            step: {
                id: 'reader_toolbar', screen: 'Reader', anchor: null, title: 'The reader', body: 'Toolbar copy.',
                advance: { kind: 'waitFor', event: { kind: 'readerReady' }, skippable: true },
            },
        })
        fireEvent.click(screen.getByText('Skip'))
        expect(controls.skip).toHaveBeenCalledTimes(1)
    })

    it('shows no forward button for a non-skippable waitFor step', () => {
        renderOverlay({
            step: {
                id: 'reader_toolbar', screen: 'Reader', anchor: null, title: 'The reader', body: 'Toolbar copy.',
                advance: { kind: 'waitFor', event: { kind: 'readerReady' }, skippable: false },
            },
        })
        expect(screen.queryByText('Skip')).not.toBeInTheDocument()
        expect(screen.queryByText('Next')).not.toBeInTheDocument()
    })

    it('shows Back only when canGoBack is true, and calls back()', () => {
        const { controls, rerender } = renderOverlay({ canGoBack: false })
        expect(screen.queryByText('Back')).not.toBeInTheDocument()

        rerender(
            <TourRegistryContext.Provider value={new TourAnchorRegistry()}>
                <TourStateContext.Provider value={{
                    state: {
                        status: 'running',
                        step: { id: 'library_filters', screen: 'Library', anchor: null, title: 'Filters', body: 'x', advance: { kind: 'next' } },
                        index: 1, total: 5, resolution: 'found', willCleanUp: false, pairId: null, canGoBack: true,
                    },
                    ...controls,
                }}>
                    <TourOverlay />
                </TourStateContext.Provider>
            </TourRegistryContext.Provider>,
        )
        fireEvent.click(screen.getByText('Back'))
        expect(controls.back).toHaveBeenCalledTimes(1)
    })

    it('shows the N of M progress indicator', () => {
        renderOverlay({ index: 2, total: 9 })
        expect(screen.getByText('3 of 9')).toBeInTheDocument()
    })

    it('calls quit() from the × button and labels it "Quit the walkthrough"', () => {
        const { controls } = renderOverlay()
        fireEvent.click(screen.getByLabelText('Quit the walkthrough'))
        expect(controls.quit).toHaveBeenCalledTimes(1)
    })

    it('calls quit() on Escape', () => {
        const { controls } = renderOverlay()
        fireEvent.keyDown(document, { key: 'Escape' })
        expect(controls.quit).toHaveBeenCalledTimes(1)
    })

    it('appends the cleanUp "put back" sentence on the done step when willCleanUp is true', () => {
        renderOverlay({
            step: {
                id: 'done', screen: 'Account', anchor: null, title: 'Done', body: 'That\'s Tandem. Enjoy your books.',
                cleanUpSuffix: ' The book we open along the way is put back the way it was when you finish.',
                advance: { kind: 'finish' },
            },
            willCleanUp: true,
        })
        expect(screen.getByText(
            'That\'s Tandem. Enjoy your books. The book we open along the way is put back the way it was when you finish.',
        )).toBeInTheDocument()
    })

    it('does not append the cleanUp sentence when willCleanUp is false', () => {
        renderOverlay({
            step: {
                id: 'done', screen: 'Account', anchor: null, title: 'Done', body: 'That\'s Tandem. Enjoy your books.',
                cleanUpSuffix: ' The book we open along the way is put back the way it was when you finish.',
                advance: { kind: 'finish' },
            },
            willCleanUp: false,
        })
        expect(screen.getByText('That\'s Tandem. Enjoy your books.')).toBeInTheDocument()
    })

    it('keeps the card inside the viewport when the hole fills it (the reader page)', () => {
        setViewport(1200)
        const reg = new TourAnchorRegistry()
        const vh = window.innerHeight
        reg.set('ReaderPage', rect({ top: 53, bottom: vh, height: vh - 53, y: 53, left: 60, right: 1100, width: 1040 }))
        renderOverlay({
            step: {
                id: 'reader_trick', screen: 'Reader', anchor: 'ReaderPage',
                title: 'Back in the reader', body: 'And it opened the page.', advance: { kind: 'next' },
            },
            resolution: 'found',
        }, { registry: reg })
        const dialog = screen.getByRole('dialog')
        expect(dialog.style.top).toBe('')
        expect(dialog.style.bottom).toBe('12px')
    })

    it('centers an anchorless card on desktop', () => {
        setViewport(1200)
        renderOverlay()
        expect(screen.getByRole('dialog').className).toContain('tour-card--centered')
    })

    it('does not center a card that has a hole, nor on mobile', () => {
        setViewport(1200)
        const reg = new TourAnchorRegistry()
        reg.set('LibraryFilterPills', rect())
        const { unmount } = renderOverlay({
            step: {
                id: 'library_filters', screen: 'Library', anchor: 'LibraryFilterPills',
                title: 'Filters', body: 'x', advance: { kind: 'next' },
            },
        }, { registry: reg })
        expect(screen.getByRole('dialog').className).not.toContain('tour-card--centered')
        unmount()
        setViewport(500)
        renderOverlay()
        expect(screen.getByRole('dialog').className).not.toContain('tour-card--centered')
        setViewport(1200)
    })

    it('gives a mobile card no inline position even with a hole: the dock is the layout', () => {
        // Seen live at phone width: the card was placed below a section that
        // sat under the fold, so the card itself ended up off-screen.
        setViewport(500)
        const reg = new TourAnchorRegistry()
        reg.set('HomeRecentlyAdded', rect({ top: 738, bottom: 870, y: 738 }))
        renderOverlay({
            step: {
                id: 'home_recently_added', screen: 'Home', anchor: 'HomeRecentlyAdded',
                title: 'Recently Added', body: 'x', advance: { kind: 'next' },
            },
        }, { registry: reg })
        const dialog = screen.getByRole('dialog')
        expect(dialog.className).toContain('tour-card--mobile')
        expect(dialog.style.top).toBe('')
        expect(dialog.style.bottom).toBe('')
        setViewport(1200)
    })

    it('docks a mobile card to the top when the hole is in the lower half (the bottom nav bar)', () => {
        setViewport(500)
        const reg = new TourAnchorRegistry()
        const vh = window.innerHeight
        reg.set('NavLibrary', rect({ top: vh - 60, bottom: vh - 6, height: 54, y: vh - 60 }))
        const { unmount } = renderOverlay({
            step: {
                id: 'home_click_library', screen: 'Home', anchor: 'NavLibrary',
                title: 'Now the Library', body: 'x', advance: { kind: 'tapAnchor', event: { kind: 'routeShown' } },
            },
        }, { registry: reg })
        expect(screen.getByRole('dialog').className).toContain('tour-card--mobile-top')
        unmount()
        reg.set('NavLibrary', rect({ top: 10, bottom: 64, height: 54, y: 10 }))
        renderOverlay({
            step: {
                id: 'home_click_library', screen: 'Home', anchor: 'NavLibrary',
                title: 'Now the Library', body: 'x', advance: { kind: 'tapAnchor', event: { kind: 'routeShown' } },
            },
        }, { registry: reg })
        expect(screen.getByRole('dialog').className).not.toContain('tour-card--mobile-top')
        setViewport(1200)
    })

    it('docks the card to the bottom under the 768px breakpoint', () => {
        setViewport(500)
        renderOverlay()
        const dialog = screen.getByRole('dialog')
        expect(dialog.className).toContain('tour-card--mobile')
        setViewport(1200)
    })

    it('does not dock the card at desktop widths', () => {
        setViewport(1200)
        renderOverlay()
        const dialog = screen.getByRole('dialog')
        expect(dialog.className).not.toContain('tour-card--mobile')
    })
})
