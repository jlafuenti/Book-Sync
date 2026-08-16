import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { AudioPlayerView, MiniPlayer } from './AudioPlayer'

const {
    getAudiobookChaptersMock, getBookmarkLogMock, getAccessTokenMock, useAudioPlayerMock, coverSrcMock,
} = vi.hoisted(() => ({
    getAudiobookChaptersMock: vi.fn(),
    getBookmarkLogMock: vi.fn(),
    getAccessTokenMock: vi.fn(() => 'token'),
    useAudioPlayerMock: vi.fn(),
    coverSrcMock: vi.fn(async (path) => (path ? `${path}?token=cover-media-token` : path)),
}))

vi.mock('../api', () => ({
    getAudiobookChapters: getAudiobookChaptersMock,
    getBookmarkLog: getBookmarkLogMock,
    getAccessToken: getAccessTokenMock,
    coverSrc: coverSrcMock,
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

// Issue #42: both transport buttons move 30 s, on the full player and the mini
// player alike. The components pass no argument -- the context owns the number.
describe('transport skip buttons', () => {
    it('the full player skips 30s in both directions', async () => {
        const skipForward = vi.fn()
        const skipBackward = vi.fn()
        useAudioPlayerMock.mockReturnValue(basePlayer({ skipForward, skipBackward }))
        render(<AudioPlayerView onClose={vi.fn()} />)
        await waitFor(() => expect(getAudiobookChaptersMock).toHaveBeenCalled())

        fireEvent.click(screen.getByTitle('Back 30s'))
        fireEvent.click(screen.getByTitle('Forward 30s'))

        expect(skipBackward).toHaveBeenCalledWith()
        expect(skipForward).toHaveBeenCalledWith()
    })

    it('the mini player skips 30s in both directions', () => {
        const skipForward = vi.fn()
        const skipBackward = vi.fn()
        useAudioPlayerMock.mockReturnValue(basePlayer({ skipForward, skipBackward, stop: vi.fn() }))
        render(<MiniPlayer onExpand={vi.fn()} />)

        fireEvent.click(screen.getByTitle('Back 30s'))
        fireEvent.click(screen.getByTitle('Forward 30s'))

        expect(skipBackward).toHaveBeenCalledWith()
        expect(skipForward).toHaveBeenCalledWith()
    })
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

// Issue #121: the player bar built its cover URL with the long-lived access
// token, which /api/files/covers has rejected (401) since covers moved to
// scoped media tokens (issue #50). Both player views must resolve the cover
// through coverSrc() like every other <img> in the app.
describe('cover art uses a scoped media token', () => {
    const coverPath = '/api/files/covers/audiobook_238.jpg'

    it('the mini player', async () => {
        useAudioPlayerMock.mockReturnValue(basePlayer({
            currentAudiobook: { id: 238, title: 'A Book', author: 'Author', coverPath, pairId: null },
            stop: vi.fn(),
        }))
        render(<MiniPlayer onExpand={vi.fn()} />)

        await waitFor(() => {
            const img = document.querySelector('.mini-player-cover img')
            expect(img).not.toBeNull()
            expect(img.getAttribute('src')).toBe(`${coverPath}?token=cover-media-token`)
        })
        expect(coverSrcMock).toHaveBeenCalledWith(coverPath)
        expect(getAccessTokenMock).not.toHaveBeenCalled()
    })

    it('the full player', async () => {
        useAudioPlayerMock.mockReturnValue(basePlayer({
            currentAudiobook: { id: 238, title: 'A Book', author: 'Author', coverPath, pairId: null },
        }))
        render(<AudioPlayerView onClose={vi.fn()} />)
        await waitFor(() => expect(getAudiobookChaptersMock).toHaveBeenCalled())

        await waitFor(() => {
            const img = document.querySelector('.audio-player-cover img')
            expect(img).not.toBeNull()
            expect(img.getAttribute('src')).toBe(`${coverPath}?token=cover-media-token`)
        })
        expect(getAccessTokenMock).not.toHaveBeenCalled()
    })
})
