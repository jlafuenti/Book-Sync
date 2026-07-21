import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { AudioPlayerProvider, useAudioPlayer } from './AudioPlayerContext'

const {
    getAudiobookStreamUrlMock, updateProgressMock, updateBookmarkMock,
    getAccessTokenMock, sendBookmarkKeepaliveMock, getDeviceIdMock, getDeviceNameMock,
} = vi.hoisted(() => ({
    getAudiobookStreamUrlMock: vi.fn(),
    updateProgressMock: vi.fn(),
    updateBookmarkMock: vi.fn(),
    getAccessTokenMock: vi.fn(() => 'token'),
    sendBookmarkKeepaliveMock: vi.fn(),
    getDeviceIdMock: vi.fn(() => 'device-123'),
    getDeviceNameMock: vi.fn(() => 'Web · Chrome'),
}))

vi.mock('../api', () => ({
    getAudiobookStreamUrl: getAudiobookStreamUrlMock,
    updateProgress: updateProgressMock,
    updateBookmark: updateBookmarkMock,
    getAccessToken: getAccessTokenMock,
    sendBookmarkKeepalive: sendBookmarkKeepaliveMock,
    getDeviceId: getDeviceIdMock,
    getDeviceName: getDeviceNameMock,
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

function Harness({ audiobook = { title: 'A Book', cover_path: null } }) {
    const player = useAudioPlayer()
    return (
        <>
            <button onClick={() => player.play(7, audiobook)}>play</button>
            <button onClick={() => player.pause()}>pause</button>
        </>
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
    getDeviceIdMock.mockReset().mockReturnValue('device-123')
    getDeviceNameMock.mockReset().mockReturnValue('Web · Chrome')
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

describe('AudioPlayerProvider heartbeat', () => {
    it('includes device_id, device_name, and captured_at in the progress and bookmark heartbeat payloads', async () => {
        // Fake only setInterval/clearInterval so the 5s heartbeat tick can be
        // advanced deterministically. Everything else (Promise resolution,
        // testing-library's waitFor) keeps using real timers/microtasks.
        vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })

        try {
            render(
                <AudioPlayerProvider>
                    <Harness audiobook={{ title: 'A Book', cover_path: null, pair_id: 99 }} />
                </AudioPlayerProvider>
            )
            fireEvent.click(screen.getByText('play'))

            await waitFor(() => expect(getAudiobookStreamUrlMock).toHaveBeenCalledWith(7))
            const audio = audioInstances[0]
            await waitFor(() => expect(audio.src).toBe('/api/files/audiobook/7?token=first-token'))

            act(() => audio.dispatchEvent(new Event('canplay')))

            audio.currentTime = 42
            act(() => { vi.advanceTimersByTime(5000) })

            expect(updateProgressMock).toHaveBeenCalledWith('audiobook', 7, expect.objectContaining({
                device_id: 'device-123',
                device_name: 'Web · Chrome',
                captured_at: expect.any(String),
            }))
            expect(updateBookmarkMock).toHaveBeenCalledWith(99, expect.objectContaining({
                device_id: 'device-123',
                device_name: 'Web · Chrome',
                captured_at: expect.any(String),
            }))
        } finally {
            vi.useRealTimers()
        }
    })
})

describe('AudioPlayerProvider unload keepalive', () => {
    it('sends device_id, device_name, and captured_at with the keepalive bookmark write on pagehide', async () => {
        render(
            <AudioPlayerProvider>
                <Harness audiobook={{ title: 'A Book', cover_path: null, pair_id: 99 }} />
            </AudioPlayerProvider>
        )
        fireEvent.click(screen.getByText('play'))

        await waitFor(() => expect(getAudiobookStreamUrlMock).toHaveBeenCalledWith(7))
        const audio = audioInstances[0]
        await waitFor(() => expect(audio.src).toBe('/api/files/audiobook/7?token=first-token'))

        act(() => audio.dispatchEvent(new Event('canplay')))
        audio.currentTime = 55

        act(() => { window.dispatchEvent(new Event('pagehide')) })

        expect(sendBookmarkKeepaliveMock).toHaveBeenCalledWith(99, expect.objectContaining({
            audio_position_ms: 55000,
            append_to_log: true,
            device_id: 'device-123',
            device_name: 'Web · Chrome',
            captured_at: expect.any(String),
        }))
    })
})

describe('AudioPlayerProvider pause()', () => {
    it('sends device_id, device_name, and captured_at with both the progress save and the bookmark log entry', async () => {
        render(
            <AudioPlayerProvider>
                <Harness audiobook={{ title: 'A Book', cover_path: null, pair_id: 99 }} />
            </AudioPlayerProvider>
        )
        fireEvent.click(screen.getByText('play'))

        await waitFor(() => expect(getAudiobookStreamUrlMock).toHaveBeenCalledWith(7))
        const audio = audioInstances[0]
        await waitFor(() => expect(audio.src).toBe('/api/files/audiobook/7?token=first-token'))

        act(() => audio.dispatchEvent(new Event('canplay')))
        audio.currentTime = 17

        fireEvent.click(screen.getByText('pause'))

        expect(updateProgressMock).toHaveBeenCalledWith('audiobook', 7, expect.objectContaining({
            audio_position_ms: 17000,
            device_id: 'device-123',
            device_name: 'Web · Chrome',
            captured_at: expect.any(String),
        }))
        expect(updateBookmarkMock).toHaveBeenCalledWith(99, expect.objectContaining({
            audio_position_ms: 17000,
            append_to_log: true,
            device_id: 'device-123',
            device_name: 'Web · Chrome',
            captured_at: expect.any(String),
        }))
    })
})

describe('AudioPlayerProvider onEnded', () => {
    it('marks progress complete and logs a finished bookmark entry, both with device_id/device_name/captured_at', async () => {
        render(
            <AudioPlayerProvider>
                <Harness audiobook={{ title: 'A Book', cover_path: null, pair_id: 99 }} />
            </AudioPlayerProvider>
        )
        fireEvent.click(screen.getByText('play'))

        await waitFor(() => expect(getAudiobookStreamUrlMock).toHaveBeenCalledWith(7))
        const audio = audioInstances[0]
        await waitFor(() => expect(audio.src).toBe('/api/files/audiobook/7?token=first-token'))

        act(() => audio.dispatchEvent(new Event('canplay')))
        audio.currentTime = 300

        act(() => audio.dispatchEvent(new Event('ended')))

        expect(updateProgressMock).toHaveBeenCalledWith('audiobook', 7, expect.objectContaining({
            is_completed: true,
            device_id: 'device-123',
            device_name: 'Web · Chrome',
            captured_at: expect.any(String),
        }))
        expect(updateBookmarkMock).toHaveBeenCalledWith(99, expect.objectContaining({
            audio_position_ms: 300000,
            append_to_log: true,
            device_id: 'device-123',
            device_name: 'Web · Chrome',
            captured_at: expect.any(String),
        }))
    })
})
