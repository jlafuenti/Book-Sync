import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { AudioPlayerProvider, useAudioPlayer } from './AudioPlayerContext'

const {
    getAudiobookStreamUrlMock, updateProgressMock, updateBookmarkMock,
    getAccessTokenMock, sendBookmarkKeepaliveMock,
} = vi.hoisted(() => ({
    getAudiobookStreamUrlMock: vi.fn(),
    updateProgressMock: vi.fn(),
    updateBookmarkMock: vi.fn(),
    getAccessTokenMock: vi.fn(() => 'token'),
    sendBookmarkKeepaliveMock: vi.fn(),
}))

vi.mock('../api', () => ({
    getAudiobookStreamUrl: getAudiobookStreamUrlMock,
    updateProgress: updateProgressMock,
    updateBookmark: updateBookmarkMock,
    getAccessToken: getAccessTokenMock,
    sendBookmarkKeepalive: sendBookmarkKeepaliveMock,
}))

// A controllable stand-in for HTMLAudioElement. Real EventTarget so the
// context's addEventListener/dispatchEvent calls behave like the browser.
class MockAudio extends EventTarget {
    constructor() {
        super()
        this.currentTime = 0
        this.duration = 0
        this.paused = true
        this.src = ''
        this.preload = ''
        this.playbackRate = 1
    }
    play() {
        this.paused = false
        this.dispatchEvent(new Event('play'))
        return Promise.resolve()
    }
    pause() {
        this.paused = true
        this.dispatchEvent(new Event('pause'))
    }
    load() {}
}

let audioInstances

function Harness() {
    const player = useAudioPlayer()
    return (
        <button onClick={() => player.play(7, { title: 'A Book', cover_path: null })}>
            play
        </button>
    )
}

beforeEach(() => {
    audioInstances = []
    vi.stubGlobal('Audio', vi.fn(function () {
        const el = new MockAudio()
        audioInstances.push(el)
        return el
    }))
    getAudiobookStreamUrlMock.mockReset().mockResolvedValue('/api/files/audiobook/7?token=first-token')
    updateProgressMock.mockReset().mockResolvedValue({})
    updateBookmarkMock.mockReset().mockResolvedValue({})
    sendBookmarkKeepaliveMock.mockReset()
})

describe('AudioPlayerProvider play()', () => {
    it('mints a stream URL, sets it as the audio src, and plays once ready', async () => {
        render(<AudioPlayerProvider><Harness /></AudioPlayerProvider>)
        fireEvent.click(screen.getByText('play'))

        await waitFor(() => expect(getAudiobookStreamUrlMock).toHaveBeenCalledWith(7))
        const audio = audioInstances[0]
        await waitFor(() => expect(audio.src).toBe('/api/files/audiobook/7?token=first-token'))

        act(() => audio.dispatchEvent(new Event('canplay')))
        expect(audio.paused).toBe(false)
    })
})

describe('AudioPlayerProvider stream-error recovery', () => {
    it('re-mints the URL and resumes from the same position after a stream error', async () => {
        getAudiobookStreamUrlMock
            .mockResolvedValueOnce('/api/files/audiobook/7?token=first-token')
            .mockResolvedValueOnce('/api/files/audiobook/7?token=refreshed-token')

        render(<AudioPlayerProvider><Harness /></AudioPlayerProvider>)
        fireEvent.click(screen.getByText('play'))

        await waitFor(() => expect(getAudiobookStreamUrlMock).toHaveBeenCalledTimes(1))
        const audio = audioInstances[0]
        act(() => audio.dispatchEvent(new Event('canplay')))
        expect(audio.paused).toBe(false)

        audio.currentTime = 123.4

        act(() => audio.dispatchEvent(new Event('error')))

        await waitFor(() => expect(getAudiobookStreamUrlMock).toHaveBeenCalledTimes(2))
        await waitFor(() => expect(audio.src).toBe('/api/files/audiobook/7?token=refreshed-token'))

        act(() => audio.dispatchEvent(new Event('canplay')))
        expect(audio.currentTime).toBe(123.4)
        expect(audio.paused).toBe(false)
    })

    it('does not attempt recovery before any audiobook has loaded', async () => {
        render(<AudioPlayerProvider><Harness /></AudioPlayerProvider>)
        // No play() call yet -- audioRef exists but currentAudiobookRef is null.
        await waitFor(() => expect(audioInstances.length).toBe(1))
        const audio = audioInstances[0]

        act(() => audio.dispatchEvent(new Event('error')))

        expect(getAudiobookStreamUrlMock).not.toHaveBeenCalled()
    })

    it('clears the recovery guard if re-minting the URL itself fails', async () => {
        getAudiobookStreamUrlMock
            .mockResolvedValueOnce('/api/files/audiobook/7?token=first-token')
            .mockRejectedValueOnce(new Error('network down'))
            .mockResolvedValueOnce('/api/files/audiobook/7?token=second-attempt')

        render(<AudioPlayerProvider><Harness /></AudioPlayerProvider>)
        fireEvent.click(screen.getByText('play'))

        await waitFor(() => expect(getAudiobookStreamUrlMock).toHaveBeenCalledTimes(1))
        const audio = audioInstances[0]
        act(() => audio.dispatchEvent(new Event('canplay')))

        act(() => audio.dispatchEvent(new Event('error')))
        await waitFor(() => expect(getAudiobookStreamUrlMock).toHaveBeenCalledTimes(2))

        // The guard must be cleared even on failure, so a second error can retry.
        act(() => audio.dispatchEvent(new Event('error')))
        await waitFor(() => expect(getAudiobookStreamUrlMock).toHaveBeenCalledTimes(3))
    })
})
