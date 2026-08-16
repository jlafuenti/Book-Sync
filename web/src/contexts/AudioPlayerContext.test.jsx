import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { AudioPlayerProvider, useAudioPlayer } from './AudioPlayerContext'

const {
    getAudiobookStreamUrlMock, updatePositionMock,
    getAccessTokenMock, sendPositionKeepaliveMock, getDeviceIdMock, getDeviceNameMock,
} = vi.hoisted(() => ({
    getAudiobookStreamUrlMock: vi.fn(),
    updatePositionMock: vi.fn(),
    getAccessTokenMock: vi.fn(() => 'token'),
    sendPositionKeepaliveMock: vi.fn(),
    getDeviceIdMock: vi.fn(() => 'device-123'),
    getDeviceNameMock: vi.fn(() => 'Web · Chrome'),
}))

vi.mock('../api', () => ({
    getAudiobookStreamUrl: getAudiobookStreamUrlMock,
    updatePosition: updatePositionMock,
    getAccessToken: getAccessTokenMock,
    sendPositionKeepalive: sendPositionKeepaliveMock,
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
            <button onClick={() => player.play(8, { title: 'Another Book', pair_id: 100 })}>play-other</button>
            <button onClick={() => player.stop()}>stop</button>
            <button onClick={() => player.setSleepTimer(1)}>sleep-1min</button>
            <button onClick={() => player.seekTo(120)}>seek-120</button>
            <button onClick={() => player.pause()}>pause</button>
            <button onClick={() => player.togglePlayPause()}>toggle</button>
            <button onClick={() => player.skipBackward()}>skip-back</button>
            <button onClick={() => player.skipForward()}>skip-fwd</button>
            <button onClick={() => player.setSpeed(1.25)}>set-speed</button>
            <button onClick={() => player.clearStaleConflict()}>clear-conflict</button>
            <div data-testid="speed">{player.speed}</div>
            <div data-testid="stale-conflict">
                {player.staleConflict ? `${player.staleConflict.deviceName}|${player.staleConflict.position}` : ''}
            </div>
        </>
    )
}

beforeEach(() => {
    localStorage.clear()
    audioInstances = []
    vi.stubGlobal('Audio', vi.fn(function () {
        const el = new MockAudio()
        audioInstances.push(el)
        return el
    }))
    getAudiobookStreamUrlMock.mockReset().mockResolvedValue('/api/files/audiobook/7?token=first-token')
    updatePositionMock.mockReset().mockResolvedValue({})
    sendPositionKeepaliveMock.mockReset()
    getDeviceIdMock.mockReset().mockReturnValue('device-123')
    getDeviceNameMock.mockReset().mockReturnValue('Web · Chrome')
})

// Issue #57: playback speed used to be React state only — every page load
// reset to 1×. It persists in localStorage under tandem_player_speed now.
describe('AudioPlayerProvider speed persistence', () => {
    it('restores the persisted speed on mount and applies it to the audio element', async () => {
        localStorage.setItem('tandem_player_speed', '1.5')
        render(<AudioPlayerProvider><Harness /></AudioPlayerProvider>)

        expect(screen.getByTestId('speed').textContent).toBe('1.5')
        expect(audioInstances[0].playbackRate).toBe(1.5)

        // A new load applies the restored speed too (play() sets it on src swap).
        fireEvent.click(screen.getByText('play'))
        await waitFor(() => expect(getAudiobookStreamUrlMock).toHaveBeenCalledTimes(1))
        expect(audioInstances[0].playbackRate).toBe(1.5)
    })

    it('persists speed changes to localStorage', () => {
        render(<AudioPlayerProvider><Harness /></AudioPlayerProvider>)
        fireEvent.click(screen.getByText('set-speed'))

        expect(audioInstances[0].playbackRate).toBe(1.25)
        expect(localStorage.getItem('tandem_player_speed')).toBe('1.25')
    })

    it('falls back to 1x when the stored value is garbage', () => {
        localStorage.setItem('tandem_player_speed', 'not-a-number')
        render(<AudioPlayerProvider><Harness /></AudioPlayerProvider>)

        expect(screen.getByTestId('speed').textContent).toBe('1')
        expect(audioInstances[0].playbackRate).toBe(1)
    })

    it('falls back to 1x when the stored value is out of range', () => {
        localStorage.setItem('tandem_player_speed', '99')
        render(<AudioPlayerProvider><Harness /></AudioPlayerProvider>)

        expect(screen.getByTestId('speed').textContent).toBe('1')
    })
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

// Issue #42: resuming mid-word after a pause is hard to follow, so a resume
// rewinds 5 s. Skip is 30 s in both directions on every surface.
describe('AudioPlayerProvider playback offsets', () => {
    async function loadedPlayer() {
        render(<AudioPlayerProvider><Harness /></AudioPlayerProvider>)
        fireEvent.click(screen.getByText('play'))
        await waitFor(() => expect(getAudiobookStreamUrlMock).toHaveBeenCalledTimes(1))
        const audio = audioInstances[0]
        act(() => audio.dispatchEvent(new Event('canplay')))
        return audio
    }

    it('rewinds 5s when resuming from a pause', async () => {
        const audio = await loadedPlayer()
        audio.currentTime = 90
        act(() => audio.pause())

        fireEvent.click(screen.getByText('toggle'))

        expect(audio.currentTime).toBe(85)
        expect(audio.paused).toBe(false)
    })

    it('clamps the resume rewind at the start of the file', async () => {
        const audio = await loadedPlayer()
        audio.currentTime = 2
        act(() => audio.pause())

        fireEvent.click(screen.getByText('toggle'))

        expect(audio.currentTime).toBe(0)
    })

    it('does not rewind when toggling from playing to paused', async () => {
        const audio = await loadedPlayer()
        audio.currentTime = 90

        fireEvent.click(screen.getByText('toggle'))

        expect(audio.paused).toBe(true)
        expect(audio.currentTime).toBe(90)
    })

    it('does not rewind on play() — that path carries an explicit position', async () => {
        const audio = await loadedPlayer()
        audio.currentTime = 90
        act(() => audio.pause())

        // Same audiobook, no position argument: play() resumes verbatim.
        fireEvent.click(screen.getByText('play'))

        await waitFor(() => expect(audio.paused).toBe(false))
        expect(audio.currentTime).toBe(90)
    })

    it('skips 30s in both directions by default', async () => {
        const audio = await loadedPlayer()
        audio.duration = 600
        audio.currentTime = 100

        fireEvent.click(screen.getByText('skip-fwd'))
        expect(audio.currentTime).toBe(130)

        fireEvent.click(screen.getByText('skip-back'))
        expect(audio.currentTime).toBe(100)
    })

    it('clamps skips at both ends of the file', async () => {
        const audio = await loadedPlayer()
        audio.duration = 600
        audio.currentTime = 10
        fireEvent.click(screen.getByText('skip-back'))
        expect(audio.currentTime).toBe(0)

        audio.currentTime = 590
        fireEvent.click(screen.getByText('skip-fwd'))
        expect(audio.currentTime).toBe(600)
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

// Issue #65: the 5s tick used to PUT on every fire (~720 writes/hour). The tick
// still runs every 5s, but a network push only goes out once NETWORK_SAVE
// _INTERVAL_MS (30s) has elapsed since the last successful one; boundaries
// (pause, seek, sleep-timer stop, book change, stop, unload) flush at once.
describe('AudioPlayerProvider heartbeat', () => {
    // Start playback with real timers (the src swap resolves through promises
    // that testing-library's waitFor polls on), THEN fake the clock so ticks
    // and the throttle window can be advanced deterministically. Date is
    // faked too, so Date.now() moves with advanceTimersByTime.
    async function startPlaying(audiobook = { title: 'A Book', cover_path: null, pair_id: 99 }) {
        render(<AudioPlayerProvider><Harness audiobook={audiobook} /></AudioPlayerProvider>)
        fireEvent.click(screen.getByText('play'))
        await waitFor(() => expect(getAudiobookStreamUrlMock).toHaveBeenCalledWith(7))
        const audio = audioInstances[0]
        await waitFor(() => expect(audio.src).toBe('/api/files/audiobook/7?token=first-token'))
        vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'setTimeout', 'clearTimeout', 'Date'] })
        act(() => audio.dispatchEvent(new Event('canplay')))
        return audio
    }

    it('does not push on the 5s tick; pushes ONE canonical position once 30s have elapsed', async () => {
        try {
            const audio = await startPlaying()
            audio.currentTime = 42

            act(() => { vi.advanceTimersByTime(5000) })
            expect(updatePositionMock).not.toHaveBeenCalled()

            act(() => { vi.advanceTimersByTime(25_000) })

            // One write, not two. As a progress write plus a bookmark write
            // they were adjudicated separately, so one could be accepted while
            // the other was rejected and the rows then disagreed permanently.
            expect(updatePositionMock).toHaveBeenCalledTimes(1)
            expect(updatePositionMock).toHaveBeenCalledWith('pair', 99, expect.objectContaining({
                source: 'audiobook',
                audio_position_ms: 42000,
                append_to_log: false,
                device_id: 'device-123',
                device_name: 'Web · Chrome',
                captured_at: expect.any(String),
            }))
        } finally {
            vi.useRealTimers()
        }
    })

    it('pushes about 10 times over 5 minutes of untouched playback, not 60', async () => {
        try {
            await startPlaying()
            for (let i = 0; i < 60; i++) {
                act(() => { vi.advanceTimersByTime(5000) })
                // Let the resolved PUT's .then run so the throttle advances.
                await act(async () => { await Promise.resolve() })
            }
            expect(updatePositionMock.mock.calls.length).toBeGreaterThanOrEqual(9)
            expect(updatePositionMock.mock.calls.length).toBeLessThanOrEqual(11)
        } finally {
            vi.useRealTimers()
        }
    })

    it('a failed push does not advance the throttle — the next tick retries', async () => {
        try {
            updatePositionMock.mockRejectedValueOnce(new Error('offline'))
            await startPlaying()

            act(() => { vi.advanceTimersByTime(30_000) })
            await act(async () => { await Promise.resolve() })
            expect(updatePositionMock).toHaveBeenCalledTimes(1)

            act(() => { vi.advanceTimersByTime(5000) })
            expect(updatePositionMock).toHaveBeenCalledTimes(2)
        } finally {
            vi.useRealTimers()
        }
    })

    it('a sleep-timer stop flushes immediately with a history entry', async () => {
        try {
            const audio = await startPlaying()
            fireEvent.click(screen.getByText('sleep-1min'))
            audio.currentTime = 30

            act(() => { vi.advanceTimersByTime(60_000) })

            expect(audio.paused).toBe(true)
            const flush = updatePositionMock.mock.calls.find(c => c[2].append_to_log === true)
            expect(flush).toBeTruthy()
            expect(flush[0]).toBe('pair')
            expect(flush[1]).toBe(99)
            expect(flush[2].audio_position_ms).toBe(30000)
        } finally {
            vi.useRealTimers()
        }
    })

    it('a seek flushes once (debounced), bypassing the 30s throttle', async () => {
        try {
            const audio = await startPlaying()
            fireEvent.click(screen.getByText('skip-fwd'))
            fireEvent.click(screen.getByText('skip-fwd'))
            fireEvent.click(screen.getByText('seek-120'))
            expect(updatePositionMock).not.toHaveBeenCalled()

            act(() => { vi.advanceTimersByTime(1500) })

            expect(updatePositionMock).toHaveBeenCalledTimes(1)
            expect(audio.currentTime).toBe(120)
            expect(updatePositionMock.mock.calls[0][2].audio_position_ms).toBe(120000)
        } finally {
            vi.useRealTimers()
        }
    })

    it('stop() flushes the outgoing book before tearing the player down', async () => {
        try {
            const audio = await startPlaying()
            audio.currentTime = 77
            fireEvent.click(screen.getByText('stop'))

            expect(updatePositionMock).toHaveBeenCalledTimes(1)
            expect(updatePositionMock).toHaveBeenCalledWith('pair', 99, expect.objectContaining({
                audio_position_ms: 77000, append_to_log: true,
            }))
        } finally {
            vi.useRealTimers()
        }
    })

    it('switching to another book flushes the one being left', async () => {
        try {
            const audio = await startPlaying()
            audio.currentTime = 66
            fireEvent.click(screen.getByText('play-other'))
            // Let the new book's stream-URL promise settle inside act.
            await act(async () => { await Promise.resolve(); await Promise.resolve() })

            expect(updatePositionMock).toHaveBeenCalledWith('pair', 99, expect.objectContaining({
                audio_position_ms: 66000, append_to_log: true,
            }))
        } finally {
            vi.useRealTimers()
        }
    })
})

describe('AudioPlayerProvider unload keepalive', () => {
    // Product rule (background saves must not hijack format routing): a
    // teardown save only claims `source` when the player was actually
    // playing at that instant. `PositionUpdate.source` is optional
    // specifically so this save can express "leave `source` alone".
    it('claims source=audiobook when still playing at unload', async () => {
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
        // MockAudio.play() (triggered by the 'canplay' handler) sets paused=false
        // and fires 'play', so the provider's `playing` state is true here.

        act(() => { window.dispatchEvent(new Event('pagehide')) })

        expect(sendPositionKeepaliveMock).toHaveBeenCalledWith('pair', 99, expect.objectContaining({
            source: 'audiobook',
            audio_position_ms: 55000,
            append_to_log: true,
            device_id: 'device-123',
            device_name: 'Web · Chrome',
            captured_at: expect.any(String),
        }))
    })

    it('omits source when paused/idle at unload', async () => {
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

        // Pause before the tab closes -- the paused/idle-player case the
        // product rule exists for (e.g. the user switched to reading, left
        // the player backgrounded and paused, then closed the tab later).
        fireEvent.click(screen.getByText('pause'))
        sendPositionKeepaliveMock.mockClear()

        act(() => { window.dispatchEvent(new Event('pagehide')) })

        const call = sendPositionKeepaliveMock.mock.calls[0]
        expect(call[0]).toBe('pair')
        expect(call[1]).toBe(99)
        expect(call[2].source).toBeUndefined()
        expect(call[2].audio_position_ms).toBe(55000)
    })
})

describe('AudioPlayerProvider pause()', () => {
    it('writes ONE canonical position carrying the log entry and device fields', async () => {
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

        expect(updatePositionMock).toHaveBeenCalledTimes(1)
        expect(updatePositionMock).toHaveBeenCalledWith('pair', 99, expect.objectContaining({
            source: 'audiobook',
            audio_position_ms: 17000,
            append_to_log: true,
            device_id: 'device-123',
            device_name: 'Web · Chrome',
            captured_at: expect.any(String),
        }))
    })
})

describe('AudioPlayerProvider stale-conflict affordance (issue #54)', () => {
    it('sets staleConflict when a write is rejected by a genuinely different device', async () => {
        updatePositionMock.mockResolvedValue({
            rejected: true,
            device_id: 'device-999',
            device_name: 'Phone',
            audio_position_ms: 90000,
        })

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

        await waitFor(() => expect(screen.getByTestId('stale-conflict').textContent).toBe('Phone|90'))
    })

    it('does not surface staleConflict when the rejection echoes this device\'s own id (a retried write)', async () => {
        updatePositionMock.mockResolvedValue({
            rejected: true,
            device_id: 'device-123', // matches getDeviceIdMock's own id
            device_name: 'Web · Chrome',
            audio_position_ms: 90000,
        })

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

        await waitFor(() => expect(updatePositionMock).toHaveBeenCalled())
        expect(screen.getByTestId('stale-conflict').textContent).toBe('')
    })

    it('clearStaleConflict resets the state (e.g. after the user clicks Jump)', async () => {
        updatePositionMock.mockResolvedValue({
            rejected: true,
            device_id: 'device-999',
            device_name: 'Phone',
            audio_position_ms: 90000,
        })

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
        await waitFor(() => expect(screen.getByTestId('stale-conflict').textContent).toBe('Phone|90'))

        fireEvent.click(screen.getByText('clear-conflict'))
        expect(screen.getByTestId('stale-conflict').textContent).toBe('')
    })
})

describe('AudioPlayerProvider onEnded', () => {
    it('marks complete, records the final position and logs the entry in ONE write', async () => {
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

        // Completion and final position in one adjudication: as two writes the
        // completion flag could land while the position was rejected as stale.
        expect(updatePositionMock).toHaveBeenCalledTimes(1)
        expect(updatePositionMock).toHaveBeenCalledWith('pair', 99, expect.objectContaining({
            source: 'audiobook',
            is_completed: true,
            audio_position_ms: 300000,
            append_to_log: true,
            device_id: 'device-123',
            device_name: 'Web · Chrome',
            captured_at: expect.any(String),
        }))
    })
})
