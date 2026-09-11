import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { AudioPlayerProvider, useAudioPlayer } from './AudioPlayerContext'

const {
    getAudiobookStreamUrlMock, updatePositionMock,
    getAccessTokenMock, sendPositionKeepaliveMock, getDeviceIdMock, getDeviceNameMock,
    coverSrcMock,
} = vi.hoisted(() => ({
    getAudiobookStreamUrlMock: vi.fn(),
    updatePositionMock: vi.fn(),
    getAccessTokenMock: vi.fn(() => 'token'),
    sendPositionKeepaliveMock: vi.fn(),
    getDeviceIdMock: vi.fn(() => 'device-123'),
    getDeviceNameMock: vi.fn(() => 'Web · Chrome'),
    coverSrcMock: vi.fn(async (path) => (path ? `${path}?token=cover-token` : path)),
}))

vi.mock('../api', () => ({
    getAudiobookStreamUrl: getAudiobookStreamUrlMock,
    updatePosition: updatePositionMock,
    getAccessToken: getAccessTokenMock,
    sendPositionKeepalive: sendPositionKeepaliveMock,
    getDeviceId: getDeviceIdMock,
    getDeviceName: getDeviceNameMock,
    coverSrc: coverSrcMock,
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
            <button onClick={() => player.retryPlayback()}>retry</button>
            <button onClick={() => player.clearPlaybackError()}>clear-playback-error</button>
            <div data-testid="speed">{player.speed}</div>
            <div data-testid="playback-error">{player.playbackError || ''}</div>
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

    // Issue #214: the re-minted source failing too used to leave
    // recoveringStreamRef latched `true` for the rest of the session -- every
    // later `error` returned early, so nothing recovered and nothing was
    // shown. Playback just sat there "paused".
    it('surfaces playbackError when the re-minted source errors too, and un-latches the guard', async () => {
        getAudiobookStreamUrlMock
            .mockResolvedValueOnce('/api/files/audiobook/7?token=first-token')
            .mockResolvedValueOnce('/api/files/audiobook/7?token=refreshed-token')
            .mockResolvedValueOnce('/api/files/audiobook/7?token=third-token')

        render(<AudioPlayerProvider><Harness /></AudioPlayerProvider>)
        fireEvent.click(screen.getByText('play'))
        await waitFor(() => expect(getAudiobookStreamUrlMock).toHaveBeenCalledTimes(1))
        const audio = audioInstances[0]
        act(() => audio.dispatchEvent(new Event('canplay')))

        // First error: the one-shot re-mint, as before.
        act(() => audio.dispatchEvent(new Event('error')))
        await waitFor(() => expect(getAudiobookStreamUrlMock).toHaveBeenCalledTimes(2))
        expect(screen.getByTestId('playback-error')).toHaveTextContent('')

        // Second error, with no canplay in between: the refreshed source is
        // broken too. That is a real failure and the listener must be told.
        act(() => audio.dispatchEvent(new Event('error')))
        await waitFor(() =>
            expect(screen.getByTestId('playback-error')).not.toHaveTextContent(''))
        // No third re-mint from that second error -- still one shot per load.
        expect(getAudiobookStreamUrlMock).toHaveBeenCalledTimes(2)

        // ...but the guard is released, so a later transient failure (after
        // playback got going again) can still self-heal.
        act(() => audio.dispatchEvent(new Event('canplay')))
        act(() => audio.dispatchEvent(new Event('error')))
        await waitFor(() => expect(getAudiobookStreamUrlMock).toHaveBeenCalledTimes(3))
    })

    it('clears playbackError once the stream plays again', async () => {
        getAudiobookStreamUrlMock
            .mockResolvedValueOnce('/api/files/audiobook/7?token=first-token')
            .mockResolvedValueOnce('/api/files/audiobook/7?token=refreshed-token')

        render(<AudioPlayerProvider><Harness /></AudioPlayerProvider>)
        fireEvent.click(screen.getByText('play'))
        await waitFor(() => expect(getAudiobookStreamUrlMock).toHaveBeenCalledTimes(1))
        const audio = audioInstances[0]
        act(() => audio.dispatchEvent(new Event('canplay')))

        act(() => audio.dispatchEvent(new Event('error')))
        await waitFor(() => expect(getAudiobookStreamUrlMock).toHaveBeenCalledTimes(2))
        act(() => audio.dispatchEvent(new Event('error')))
        await waitFor(() =>
            expect(screen.getByTestId('playback-error')).not.toHaveTextContent(''))

        act(() => audio.dispatchEvent(new Event('canplay')))

        await waitFor(() => expect(screen.getByTestId('playback-error')).toHaveTextContent(''))
    })

    it('reports the failure when re-minting the URL itself fails', async () => {
        getAudiobookStreamUrlMock
            .mockResolvedValueOnce('/api/files/audiobook/7?token=first-token')
            .mockRejectedValueOnce(new Error('network down'))

        render(<AudioPlayerProvider><Harness /></AudioPlayerProvider>)
        fireEvent.click(screen.getByText('play'))
        await waitFor(() => expect(getAudiobookStreamUrlMock).toHaveBeenCalledTimes(1))
        const audio = audioInstances[0]
        act(() => audio.dispatchEvent(new Event('canplay')))

        act(() => audio.dispatchEvent(new Event('error')))

        await waitFor(() =>
            expect(screen.getByTestId('playback-error')).not.toHaveTextContent(''))
    })

    it('retryPlayback re-mints the stream and resumes at the same position', async () => {
        getAudiobookStreamUrlMock
            .mockResolvedValueOnce('/api/files/audiobook/7?token=first-token')
            .mockResolvedValueOnce('/api/files/audiobook/7?token=refreshed-token')
            .mockResolvedValueOnce('/api/files/audiobook/7?token=retry-token')

        render(<AudioPlayerProvider><Harness /></AudioPlayerProvider>)
        fireEvent.click(screen.getByText('play'))
        await waitFor(() => expect(getAudiobookStreamUrlMock).toHaveBeenCalledTimes(1))
        const audio = audioInstances[0]
        act(() => audio.dispatchEvent(new Event('canplay')))
        audio.currentTime = 321.5

        act(() => audio.dispatchEvent(new Event('error')))
        await waitFor(() => expect(getAudiobookStreamUrlMock).toHaveBeenCalledTimes(2))
        act(() => audio.dispatchEvent(new Event('error')))
        await waitFor(() =>
            expect(screen.getByTestId('playback-error')).not.toHaveTextContent(''))
        act(() => audio.pause())

        fireEvent.click(screen.getByText('retry'))

        await waitFor(() => expect(getAudiobookStreamUrlMock).toHaveBeenCalledTimes(3))
        await waitFor(() => expect(audio.src).toBe('/api/files/audiobook/7?token=retry-token'))
        act(() => audio.dispatchEvent(new Event('canplay')))
        // A retry is a transparent refresh, not a user resume: exact position,
        // no rewind (contract § Playback offsets).
        expect(audio.currentTime).toBe(321.5)
        expect(audio.paused).toBe(false)
        expect(screen.getByTestId('playback-error')).toHaveTextContent('')
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
        // `shouldClearNativeTimers` is NOT optional here, and it is not a
        // default we inherit: passing an options object to `useFakeTimers`
        // *replaces* vitest's `fakeTimers` config rather than merging with it,
        // so naming `toFake` silently drops it.
        //
        // Without it the heartbeat interval leaks. It is created under real
        // timers (above), so its id is a native handle; when the provider's
        // effect re-runs after the swap — a book change or a stop does that —
        // the now-fake `clearInterval` cannot cancel a native handle, and a
        // live 5s interval closed over the finished test's audio element
        // survives into the rest of the run. It then fires on the real clock
        // and pushes a position from the wrong book, which is a failure that
        // only appears when the suite runs slowly enough for five real seconds
        // to elapse mid-test — green locally, red under load.
        vi.useFakeTimers({
            toFake: ['setInterval', 'clearInterval', 'setTimeout', 'clearTimeout', 'Date'],
            shouldClearNativeTimers: true,
        })
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

    // Issue #158: on iOS the PWA is the app — beforeunload never fires and
    // pagehide is unreliable; visibilitychange → hidden is the last
    // guaranteed event, and nothing listened for it.
    it('visibilitychange → hidden sends the same keepalive as pagehide', async () => {
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

        Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' })
        try {
            act(() => { document.dispatchEvent(new Event('visibilitychange')) })

            expect(sendPositionKeepaliveMock).toHaveBeenCalledWith('pair', 99, expect.objectContaining({
                source: 'audiobook',
                audio_position_ms: 55000,
                append_to_log: true,
                device_id: 'device-123',
                device_name: 'Web · Chrome',
                captured_at: expect.any(String),
            }))
        } finally {
            delete document.visibilityState
        }
    })

    it('a repeated lifecycle event with no playback in between writes nothing', async () => {
        // visibilitychange fires on every tab switch and the payload carries
        // append_to_log: true — without idempotence each one would spam the
        // session history.
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
        expect(sendPositionKeepaliveMock).toHaveBeenCalledTimes(1)

        act(() => { window.dispatchEvent(new Event('pagehide')) })
        expect(sendPositionKeepaliveMock).toHaveBeenCalledTimes(1)

        // Playback moved on → the next event writes again.
        audio.currentTime = 60
        act(() => { window.dispatchEvent(new Event('pagehide')) })
        expect(sendPositionKeepaliveMock).toHaveBeenCalledTimes(2)
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

        // Scope the count to the click (issue #132). "ONE write" is a claim
        // about what pause() does, but the assertion was counting every call
        // since the render — so anything else that pushed while the test was
        // getting here (the 5s heartbeat's 30s throttle elapsing on a stalled
        // CI worker, a straggling continuation from an earlier test landing
        // after beforeEach's reset) failed it without pause() being wrong.
        // The keepalive test above already clears immediately before its
        // action for the same reason.
        updatePositionMock.mockClear()

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

// Issue #469: two timers are scheduled into refs and cleared only when they are
// re-scheduled or fire — never on unmount. A leaked seek flush writes a position
// for a book the user has already left, which is the silent wrong-position
// failure docs/position-sync-contract.md exists to prevent. It is also what made
// this suite fail at random under load: the 1s debounce lands inside whichever
// test happens to be running a second later, and the write carries the *previous*
// test's book (scope 'audiobook', id 7) into an assertion about a different one.
describe('AudioPlayerProvider unmount teardown', () => {
    // Load under real timers — the src swap resolves through promises that
    // waitFor polls — then fake the clock so the pending timer is a fake one
    // that can be advanced deterministically instead of slept through.
    async function loadedPlayer() {
        const { unmount } = render(<AudioPlayerProvider><Harness /></AudioPlayerProvider>)
        fireEvent.click(screen.getByText('play'))
        await waitFor(() => expect(getAudiobookStreamUrlMock).toHaveBeenCalledTimes(1))
        const audio = audioInstances[0]
        audio.duration = 600
        act(() => audio.dispatchEvent(new Event('canplay')))
        vi.useFakeTimers({
            toFake: ['setInterval', 'clearInterval', 'setTimeout', 'clearTimeout', 'Date'],
            shouldClearNativeTimers: true,
        })
        return { audio, unmount }
    }

    it('does not flush a pending seek after the provider unmounts', async () => {
        try {
            const { audio, unmount } = await loadedPlayer()
            audio.currentTime = 100
            fireEvent.click(screen.getByText('skip-fwd'))  // schedules the 1s debounce

            // Anything written during load is not what this test is about.
            updatePositionMock.mockReset().mockResolvedValue({})
            unmount()
            act(() => { vi.advanceTimersByTime(5000) })

            expect(updatePositionMock).not.toHaveBeenCalled()
        } finally {
            vi.useRealTimers()
        }
    })

    it('does not fire a sleep timer after the provider unmounts', async () => {
        try {
            const { unmount } = await loadedPlayer()
            fireEvent.click(screen.getByText('sleep-1min'))

            updatePositionMock.mockReset().mockResolvedValue({})
            unmount()
            act(() => { vi.advanceTimersByTime(2 * 60 * 1000) })

            expect(updatePositionMock).not.toHaveBeenCalled()
        } finally {
            vi.useRealTimers()
        }
    })
})

// Issue #62: lock-screen / notification / hardware-key controls. The OS drives
// the *same* context functions the on-screen transport uses, so the two never
// disagree about skip amounts, resume rewind, or which format a save claims.
describe('AudioPlayerProvider media session', () => {
    let ms

    beforeEach(() => {
        ms = {
            metadata: null,
            playbackState: 'none',
            setActionHandler: vi.fn(),
            setPositionState: vi.fn(),
        }
        // jsdom's navigator has no mediaSession; add one for the test.
        Object.defineProperty(navigator, 'mediaSession', { value: ms, configurable: true, writable: true })
        vi.stubGlobal('MediaMetadata', vi.fn(function (init) { Object.assign(this, init) }))
    })

    afterEach(() => {
        delete navigator.mediaSession
        vi.unstubAllGlobals()
    })

    // The handler currently bound for an action (null after an unbind).
    function handler(action) {
        const call = [...ms.setActionHandler.mock.calls].reverse().find(([a]) => a === action)
        return (call && call[1]) || undefined
    }

    async function loadedPlayer(audiobook = { title: 'Ship of Magic', author: 'Robin Hobb', cover_path: '/api/files/covers/audiobook_7.jpg' }) {
        render(<AudioPlayerProvider><Harness audiobook={audiobook} /></AudioPlayerProvider>)
        fireEvent.click(screen.getByText('play'))
        await waitFor(() => expect(getAudiobookStreamUrlMock).toHaveBeenCalledTimes(1))
        const audio = audioInstances[0]
        audio.duration = 3600
        act(() => audio.dispatchEvent(new Event('canplay')))
        return audio
    }

    it('publishes title, author and a media-token cover once a book loads', async () => {
        await loadedPlayer()
        await waitFor(() => expect(ms.metadata).not.toBeNull())
        expect(ms.metadata.title).toBe('Ship of Magic')
        expect(ms.metadata.artist).toBe('Robin Hobb')
        // The cover goes through the same scoped-token helper as every other
        // <img> in the app (issue #50) — never the long-lived access token.
        expect(ms.metadata.artwork[0].src).toBe('/api/files/covers/audiobook_7.jpg?token=cover-token')
        expect(ms.metadata.artwork[0].src).not.toContain('token=token')
    })

    it('binds handlers for play, pause, stop, seek back/forward and seekto', async () => {
        await loadedPlayer()
        for (const action of ['play', 'pause', 'stop', 'seekbackward', 'seekforward', 'seekto']) {
            expect(handler(action)).toEqual(expect.any(Function))
        }
    })

    it('skips 30 s in each direction — the contract amount, not the OS default', async () => {
        const audio = await loadedPlayer()
        audio.currentTime = 100
        act(() => handler('seekforward')({ action: 'seekforward' }))
        expect(audio.currentTime).toBe(130)
        act(() => handler('seekbackward')({ action: 'seekbackward' }))
        expect(audio.currentTime).toBe(100)
    })

    it('seekto lands on the requested time', async () => {
        const audio = await loadedPlayer()
        act(() => handler('seekto')({ action: 'seekto', seekTime: 1234 }))
        expect(audio.currentTime).toBe(1234)
    })

    it('a lock-screen play resumes with the 5 s rewind, and pause pauses', async () => {
        const audio = await loadedPlayer()
        audio.currentTime = 90
        act(() => handler('pause')({ action: 'pause' }))
        expect(audio.paused).toBe(true)

        act(() => handler('play')({ action: 'play' }))
        expect(audio.paused).toBe(false)
        expect(audio.currentTime).toBe(85)
    })

    it('mirrors playback state and feeds the scrubber on timeupdate', async () => {
        const audio = await loadedPlayer()
        await waitFor(() => expect(ms.playbackState).toBe('playing'))

        audio.currentTime = 42
        audio.playbackRate = 1.25
        act(() => audio.dispatchEvent(new Event('timeupdate')))
        expect(ms.setPositionState).toHaveBeenLastCalledWith({ duration: 3600, position: 42, playbackRate: 1.25 })

        act(() => audio.pause())
        expect(ms.playbackState).toBe('paused')
    })

    it('stop clears the session so the OS drops the controls', async () => {
        await loadedPlayer()
        await waitFor(() => expect(ms.metadata).not.toBeNull())

        fireEvent.click(screen.getByText('stop'))

        expect(ms.metadata).toBeNull()
        expect(ms.playbackState).toBe('none')
        expect(handler('play')).toBeUndefined()
    })

    it('does nothing (and does not throw) without the API', async () => {
        delete navigator.mediaSession
        const audio = await loadedPlayer()
        act(() => audio.dispatchEvent(new Event('timeupdate')))
        fireEvent.click(screen.getByText('stop'))
        expect(ms.setActionHandler).not.toHaveBeenCalled()
    })
})
