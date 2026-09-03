import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'

// ---------------------------------------------------------------------------
// Issue #267: the app-wide mini-player had no way back to the text.
//
// AppMiniPlayer is mounted in AppShell, so it is the player every route other
// than Home and BookDetail gets — start a book on Home, walk to /library,
// expand the mini-player, and the "Read" button was simply gone. Whether the
// audio->text handoff existed depended on which page you happened to be
// standing on, while Android offers it unconditionally for a pair.
//
// The mini-player is route-independent (mounted once, outside <Routes>), so
// rendering it directly under a router at /library is the same thing the shell
// does — without dragging every page module into the test.
// ---------------------------------------------------------------------------

const {
    useAudioPlayerMock, updatePositionMock, getAudiobookChaptersMock, getBookmarkLogMock,
} = vi.hoisted(() => ({
    useAudioPlayerMock: vi.fn(),
    updatePositionMock: vi.fn(),
    getAudiobookChaptersMock: vi.fn(),
    getBookmarkLogMock: vi.fn(),
}))

vi.mock('./contexts/AudioPlayerContext', async (importOriginal) => ({
    ...await importOriginal(),
    useAudioPlayer: useAudioPlayerMock,
}))

vi.mock('./api', async (importOriginal) => ({
    ...await importOriginal(),
    updatePosition: updatePositionMock,
    getAudiobookChapters: getAudiobookChaptersMock,
    getBookmarkLog: getBookmarkLogMock,
    getDeviceId: () => 'device-abc',
    getDeviceName: () => 'Web · Chrome',
}))

function basePlayer(overrides = {}) {
    return {
        currentAudiobook: { id: 7, title: 'Antiagon Fire', author: 'Modesitt', coverPath: null, pairId: 77 },
        pairedEbookId: 900,
        playing: true,
        currentTime: 61.5,
        duration: 3600,
        speed: 1,
        sleepMinutes: null,
        staleConflict: null,
        pause: vi.fn(),
        seekTo: vi.fn(),
        clearStaleConflict: vi.fn(),
        togglePlayPause: vi.fn(),
        skipForward: vi.fn(),
        skipBackward: vi.fn(),
        setSpeed: vi.fn(),
        setSleepTimer: vi.fn(),
        ...overrides,
    }
}

function LocationProbe() {
    const location = useLocation()
    return (
        <div
            data-testid="location"
            data-pathname={location.pathname}
            data-state={JSON.stringify(location.state ?? null)}
        />
    )
}

async function renderMiniPlayer(player) {
    useAudioPlayerMock.mockReturnValue(player)
    const { AppMiniPlayer } = await import('./App')
    render(
        <MemoryRouter initialEntries={['/library']}>
            <AppMiniPlayer />
            <LocationProbe />
        </MemoryRouter>,
    )
}

function locationState() {
    return JSON.parse(screen.getByTestId('location').getAttribute('data-state'))
}

beforeEach(() => {
    useAudioPlayerMock.mockReset()
    updatePositionMock.mockReset().mockResolvedValue({ epub_chapter: 5 })
    getAudiobookChaptersMock.mockReset().mockResolvedValue([])
    getBookmarkLogMock.mockReset().mockResolvedValue([])
})

describe('AppMiniPlayer — switch to ebook (issue #267)', () => {
    it('offers the control in the expanded player when the book is paired', async () => {
        await renderMiniPlayer(basePlayer())

        fireEvent.click(screen.getByText('Antiagon Fire'))

        expect(await screen.findByTitle('Switch to Ebook')).toBeTruthy()
    })

    it('hands the position over and opens the reader on the paired ebook', async () => {
        const player = basePlayer()
        await renderMiniPlayer(player)
        fireEvent.click(screen.getByText('Antiagon Fire'))
        fireEvent.click(await screen.findByTitle('Switch to Ebook'))

        await waitFor(() => expect(updatePositionMock).toHaveBeenCalledWith(
            'pair', 77,
            expect.objectContaining({
                source: 'audiobook',
                audio_position_ms: 61500,
                device_id: 'device-abc',
            }),
        ))
        // Pausing first is what stops the audio running on under the reader,
        // and stops the heartbeat writing a newer position over the handoff.
        expect(player.pause).toHaveBeenCalled()

        await waitFor(() => {
            expect(screen.getByTestId('location').getAttribute('data-pathname')).toBe('/book/ebook/900')
        })
        expect(locationState()).toEqual({ openReader: true, initialChapter: 5 })
    })

    it('still navigates when the position write fails', async () => {
        // Offline, or the server refusing: the user asked to read, so read.
        // The reader falls back to whatever position it already knows.
        updatePositionMock.mockRejectedValue(new Error('offline'))
        await renderMiniPlayer(basePlayer())
        fireEvent.click(screen.getByText('Antiagon Fire'))
        fireEvent.click(await screen.findByTitle('Switch to Ebook'))

        await waitFor(() => {
            expect(screen.getByTestId('location').getAttribute('data-pathname')).toBe('/book/ebook/900')
        })
        expect(locationState()).toEqual({ openReader: true, initialChapter: null })
    })

    it('omits the control for an audiobook with no paired ebook', async () => {
        await renderMiniPlayer(basePlayer({ pairedEbookId: null }))

        fireEvent.click(screen.getByText('Antiagon Fire'))

        expect(await screen.findByTitle('Close player')).toBeTruthy()
        expect(screen.queryByTitle('Switch to Ebook')).toBeNull()
    })

    it('renders nothing at all when no audiobook is loaded', async () => {
        await renderMiniPlayer(basePlayer({ currentAudiobook: null }))

        expect(screen.queryByText('Antiagon Fire')).toBeNull()
    })
})
