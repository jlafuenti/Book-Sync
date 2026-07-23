import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { AudioPlayerView } from './AudioPlayer'

const {
    getAudiobookChaptersMock, getBookmarkLogMock, getAccessTokenMock, useAudioPlayerMock,
} = vi.hoisted(() => ({
    getAudiobookChaptersMock: vi.fn(),
    getBookmarkLogMock: vi.fn(),
    getAccessTokenMock: vi.fn(() => 'token'),
    useAudioPlayerMock: vi.fn(),
}))

vi.mock('../api', () => ({
    getAudiobookChapters: getAudiobookChaptersMock,
    getBookmarkLog: getBookmarkLogMock,
    getAccessToken: getAccessTokenMock,
}))

vi.mock('../contexts/AudioPlayerContext', () => ({
    useAudioPlayer: useAudioPlayerMock,
}))

function basePlayer(overrides = {}) {
    return {
        currentAudiobook: { id: 7, title: 'A Book', author: 'Author', coverPath: null, pairId: 99 },
        pairedEbookId: null,
        playing: false,
        currentTime: 0,
        duration: 100,
        speed: 1,
        sleepMinutes: null,
        staleConflict: null,
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

beforeEach(() => {
    getAudiobookChaptersMock.mockReset().mockResolvedValue([])
    getBookmarkLogMock.mockReset().mockResolvedValue([])
    getAccessTokenMock.mockReset().mockReturnValue('token')
})

describe('AudioPlayerView stale-conflict banner (issue #54)', () => {
    it('renders nothing when there is no staleConflict', async () => {
        useAudioPlayerMock.mockReturnValue(basePlayer())
        render(<AudioPlayerView onClose={vi.fn()} />)
        await waitFor(() => expect(getAudiobookChaptersMock).toHaveBeenCalled())
        expect(screen.queryByText(/Newer position available/)).not.toBeInTheDocument()
    })

    it('renders the banner text with the device name from staleConflict', async () => {
        useAudioPlayerMock.mockReturnValue(basePlayer({
            staleConflict: { position: 123.4, deviceName: 'Phone' },
        }))
        render(<AudioPlayerView onClose={vi.fn()} />)
        await waitFor(() => expect(getAudiobookChaptersMock).toHaveBeenCalled())
        expect(screen.getByText(/Newer position available from Phone/)).toBeInTheDocument()
        expect(screen.getByText('Jump')).toBeInTheDocument()
    })

    it('Jump seeks to the server position and dismisses the banner -- never auto-seeks', async () => {
        const seekTo = vi.fn()
        const clearStaleConflict = vi.fn()
        useAudioPlayerMock.mockReturnValue(basePlayer({
            staleConflict: { position: 123.4, deviceName: 'Phone' },
            seekTo,
            clearStaleConflict,
        }))
        render(<AudioPlayerView onClose={vi.fn()} />)
        await waitFor(() => expect(getAudiobookChaptersMock).toHaveBeenCalled())

        // Rendering the banner alone must never seek.
        expect(seekTo).not.toHaveBeenCalled()

        fireEvent.click(screen.getByText('Jump'))
        expect(seekTo).toHaveBeenCalledWith(123.4)
        expect(clearStaleConflict).toHaveBeenCalledTimes(1)
    })
})

describe('AudioPlayerView Session History device attribution (issue #54)', () => {
    it('shows device_name per entry, falls back to device_id, and omits the label when both are absent', async () => {
        getBookmarkLogMock.mockResolvedValue([
            { changed_at: '2024-01-01T00:00:00Z', new_audio_position_ms: 1000, device_name: 'Phone', device_id: 'dev-1' },
            { changed_at: '2024-01-02T00:00:00Z', new_audio_position_ms: 2000, device_id: 'dev-2' },
            { changed_at: '2024-01-03T00:00:00Z', new_audio_position_ms: 3000 },
        ])
        useAudioPlayerMock.mockReturnValue(basePlayer())
        render(<AudioPlayerView onClose={vi.fn()} />)

        fireEvent.click(screen.getByTitle('Session history'))
        await waitFor(() => expect(getBookmarkLogMock).toHaveBeenCalledWith(99))

        await waitFor(() => expect(screen.getByText(/from Phone/)).toBeInTheDocument())
        expect(screen.getByText(/from dev-2/)).toBeInTheDocument()

        // Neither device_name nor device_id present -- must not render "from"
        // at all (no "from null" / "from undefined").
        expect(screen.queryByText(/from undefined/)).not.toBeInTheDocument()
        expect(screen.queryByText(/from null/)).not.toBeInTheDocument()
        expect(screen.queryByText(/from$/)).not.toBeInTheDocument()
    })
})
