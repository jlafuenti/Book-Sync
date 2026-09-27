import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route, Link, useLocation } from 'react-router-dom'
import { AuthProvider } from '../contexts/AuthContext'
import { TourProvider } from './TourProvider'
import { useTour } from './TourContext'
import { useTourEmit, TourEvents } from './anchors'

const { updateMeMock, getPairsMock, getPositionMock, resetPairProgressMock } = vi.hoisted(() => ({
    updateMeMock: vi.fn().mockResolvedValue({}),
    getPairsMock: vi.fn().mockResolvedValue([]),
    getPositionMock: vi.fn().mockResolvedValue(null),
    resetPairProgressMock: vi.fn().mockResolvedValue({}),
}))
vi.mock('../api/auth', () => ({ updateMe: updateMeMock }))
vi.mock('../api/library', () => ({ getPairs: getPairsMock }))
vi.mock('../api/sync', () => ({ getPosition: getPositionMock, resetPairProgress: resetPairProgressMock }))

function Probe() {
    const { state, start, next, quit, adoptPair } = useTour()
    const emit = useTourEmit()
    return (
        <div>
            <div data-testid="tour-status">{state.status}</div>
            <div data-testid="tour-step">{state.step?.id || ''}</div>
            <div data-testid="tour-pair-id">{state.pairId ?? ''}</div>
            <button onClick={start}>start-tour</button>
            <button onClick={next}>next-step</button>
            <button onClick={quit}>quit-tour</button>
            <button onClick={() => adoptPair({ id: 42 })}>adopt-pair-42</button>
            <button onClick={() => emit(TourEvents.detailsOpened(1))}>emit-details-opened</button>
            <button onClick={() => emit(TourEvents.readerOpened(1))}>emit-reader-opened</button>
            <button onClick={() => emit(TourEvents.readerReady())}>emit-reader-ready</button>
            <button onClick={() => emit(TourEvents.readerProgressModeChanged())}>emit-reader-progress</button>
            <button onClick={() => emit(TourEvents.playerOpened(1))}>emit-player-opened</button>
            <button onClick={() => emit(TourEvents.playerReady())}>emit-player-ready</button>
        </div>
    )
}

function LocationProbe() {
    const loc = useLocation()
    return <div data-testid="pathname" data-state={JSON.stringify(loc.state)}>{loc.pathname}</div>
}

function renderProvider(user, { initialEntry = '/continue' } = {}) {
    return render(
        <MemoryRouter initialEntries={[initialEntry]}>
            <AuthProvider user={user}>
                <TourProvider>
                    <Probe />
                    <LocationProbe />
                    <Routes>
                        <Route path="/continue" element={<Link to="/library">go-library</Link>} />
                        <Route path="/library" element={<>
                            <Link to="/series">go-series</Link>
                            <Link to="/system">go-system</Link>
                        </>} />
                        <Route path="/series" element={<Link to="/transcription">go-transcription</Link>} />
                        <Route path="/transcription" element={<div>transcription-stub</div>} />
                        <Route path="/system/troubleshoot" element={<div>troubleshoot-stub</div>} />
                        <Route path="/system" element={<div>system-stub</div>} />
                        <Route path="/account" element={<div>account-stub</div>} />
                    </Routes>
                </TourProvider>
            </AuthProvider>
        </MemoryRouter>,
    )
}

beforeEach(() => {
    updateMeMock.mockClear()
    getPairsMock.mockClear().mockResolvedValue([])
    getPositionMock.mockClear().mockResolvedValue(null)
})

describe('TourProvider — the one-time offer', () => {
    it('shows the offer when web_tour_offered_at is exactly null', () => {
        renderProvider({ username: 'alice', role: 'user', web_tour_offered_at: null })
        expect(screen.getByText('Take the 5-minute tour?')).toBeInTheDocument()
    })

    it('does not show the offer when web_tour_offered_at is undefined (an older server)', () => {
        renderProvider({ username: 'alice', role: 'user' })
        expect(screen.queryByText('Take the 5-minute tour?')).not.toBeInTheDocument()
    })

    it('does not show the offer once it has already been answered', () => {
        renderProvider({ username: 'alice', role: 'user', web_tour_offered_at: '2026-01-01T00:00:00Z' })
        expect(screen.queryByText('Take the 5-minute tour?')).not.toBeInTheDocument()
    })

    it('"Not now" calls updateMe and dismisses the offer without starting the tour', () => {
        renderProvider({ username: 'alice', role: 'user', web_tour_offered_at: null })
        fireEvent.click(screen.getByText('Not now'))
        expect(updateMeMock).toHaveBeenCalledWith({ web_tour_offered: true })
        expect(screen.queryByText('Take the 5-minute tour?')).not.toBeInTheDocument()
        expect(screen.getByTestId('tour-status')).toHaveTextContent('idle')
    })

    it('"Take the tour" calls updateMe and starts the tour', () => {
        renderProvider({ username: 'alice', role: 'user', web_tour_offered_at: null })
        fireEvent.click(screen.getByText('Take the tour'))
        expect(updateMeMock).toHaveBeenCalledWith({ web_tour_offered: true })
        expect(screen.getByTestId('tour-status')).toHaveTextContent('preparing')
        expect(screen.getByTestId('tour-step')).toHaveTextContent('welcome')
    })
})

describe('TourProvider — routing', () => {
    it('emits routeShown on navigation, advancing a tapAnchor(routeShown) step', async () => {
        renderProvider({ username: 'alice', role: 'user' }, { initialEntry: '/continue' })
        fireEvent.click(screen.getByText('start-tour'))
        await waitFor(() => expect(screen.getByTestId('tour-status')).toHaveTextContent('running'))

        for (let i = 0; i < 4; i++) fireEvent.click(screen.getByText('next-step'))
        expect(screen.getByTestId('tour-step')).toHaveTextContent('home_click_library')

        fireEvent.click(screen.getByText('go-library'))
        expect(screen.getByTestId('tour-step')).toHaveTextContent('library_filters')
        expect(screen.getByTestId('pathname')).toHaveTextContent('/library')
    })

    it('executes a goTo nav request by navigating the real router (troubleshoot_page, editor role)', async () => {
        renderProvider({ username: 'alice', role: 'editor' }, { initialEntry: '/continue' })
        fireEvent.click(screen.getByText('start-tour'))
        await waitFor(() => expect(screen.getByTestId('tour-status')).toHaveTextContent('running'))

        const actions = [
            'next-step', 'next-step', 'next-step', 'next-step',
            'go-library',
            'next-step', 'next-step', 'next-step',
            'emit-details-opened',
            'next-step',
            'emit-reader-opened',
            'emit-reader-ready',
            'emit-reader-progress',
            'emit-player-opened',
            'emit-player-ready',
            'emit-reader-opened',
            'next-step',
            'go-series',
            'next-step',
            'go-transcription',
            'next-step',
            'next-step',
        ]
        for (const action of actions) {
            fireEvent.click(screen.getByText(action))
        }

        expect(screen.getByTestId('tour-step')).toHaveTextContent('troubleshoot_page')
        expect(screen.getByTestId('pathname')).toHaveTextContent('/system/troubleshoot')
    })
})

describe('TourProvider — closeOverlays consumption (issue #598 Track B)', () => {
    it('quitting out of the reader replaces location state with closeOverlays: true, for the reader/player surface to consume', async () => {
        renderProvider({ username: 'alice', role: 'user' }, { initialEntry: '/continue' })
        fireEvent.click(screen.getByText('start-tour'))
        await waitFor(() => expect(screen.getByTestId('tour-status')).toHaveTextContent('running'))

        for (let i = 0; i < 4; i++) fireEvent.click(screen.getByText('next-step'))
        fireEvent.click(screen.getByText('go-library'))
        fireEvent.click(screen.getByText('next-step')) // library_sort_and_search
        fireEvent.click(screen.getByText('next-step')) // library_open_book (maintenance is editor-only, filtered for a plain user)
        fireEvent.click(screen.getByText('emit-details-opened'))
        fireEvent.click(screen.getByText('next-step')) // details_click_read
        fireEvent.click(screen.getByText('emit-reader-opened'))
        expect(screen.getByTestId('tour-step')).toHaveTextContent('reader_toolbar')

        fireEvent.click(screen.getByText('quit-tour'))

        expect(screen.getByTestId('pathname')).toHaveTextContent('/library')
        expect(JSON.parse(screen.getByTestId('pathname').dataset.state)).toEqual({ closeOverlays: true })
    })
})

describe('TourProvider — adoptPair (issue #598 Track B, Library re-adoption)', () => {
    it('exposes adoptPair() through useTour(), updating state.pairId and re-checking willCleanUp', async () => {
        renderProvider({ username: 'alice', role: 'user' }, { initialEntry: '/continue' })
        fireEvent.click(screen.getByText('start-tour'))
        await waitFor(() => expect(screen.getByTestId('tour-status')).toHaveTextContent('running'))

        fireEvent.click(screen.getByText('adopt-pair-42'))

        expect(screen.getByTestId('tour-pair-id')).toHaveTextContent('42')
        await waitFor(() => expect(getPositionMock).toHaveBeenCalledWith('pair', 42))
    })
})
