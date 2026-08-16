import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
    MEDIA_SESSION_ACTIONS,
    mediaSessionAvailable,
    applyMetadata,
    applyPositionState,
    setPlaybackState,
    bindActionHandlers,
    clearMediaSession,
} from './mediaSession'

// Pure wrappers around navigator.mediaSession (issue #62). Kept out of the
// React context so the browser-API edge cases — missing API, unsupported
// actions throwing, non-finite durations — are pinned without a component.

function installMediaSession() {
    const ms = {
        metadata: null,
        playbackState: 'none',
        setActionHandler: vi.fn(),
        setPositionState: vi.fn(),
    }
    vi.stubGlobal('navigator', { ...navigator, mediaSession: ms })
    vi.stubGlobal('MediaMetadata', vi.fn(function (init) { Object.assign(this, init) }))
    return ms
}

afterEach(() => {
    vi.unstubAllGlobals()
})

describe('feature detection', () => {
    it('reports unavailable when navigator has no mediaSession', () => {
        vi.stubGlobal('navigator', { userAgent: 'jsdom' })
        expect(mediaSessionAvailable()).toBe(false)
        // Every helper is a silent no-op — the app must not care.
        expect(() => {
            applyMetadata({ title: 'T', artist: 'A', artworkUrl: null })
            applyPositionState({ duration: 10, position: 1, playbackRate: 1 })
            setPlaybackState('playing')
            bindActionHandlers({ play: () => {} })
            clearMediaSession()
        }).not.toThrow()
    })

    it('reports available when it exists', () => {
        installMediaSession()
        expect(mediaSessionAvailable()).toBe(true)
    })
})

describe('applyMetadata', () => {
    let ms
    beforeEach(() => { ms = installMediaSession() })

    it('sets title, artist and artwork on a MediaMetadata', () => {
        applyMetadata({ title: 'Ship of Magic', artist: 'Robin Hobb', artworkUrl: '/api/files/covers/x.jpg?token=t' })
        expect(ms.metadata.title).toBe('Ship of Magic')
        expect(ms.metadata.artist).toBe('Robin Hobb')
        expect(ms.metadata.artwork).toEqual([{ src: '/api/files/covers/x.jpg?token=t' }])
    })

    it('omits artwork when there is no cover', () => {
        applyMetadata({ title: 'T', artist: '', artworkUrl: null })
        expect(ms.metadata.artwork).toEqual([])
    })
})

describe('applyPositionState', () => {
    let ms
    beforeEach(() => { ms = installMediaSession() })

    it('forwards duration/position/rate', () => {
        applyPositionState({ duration: 3600, position: 120, playbackRate: 1.25 })
        expect(ms.setPositionState).toHaveBeenCalledWith({ duration: 3600, position: 120, playbackRate: 1.25 })
    })

    it('skips a non-finite or zero duration (a stream that has not reported one yet)', () => {
        applyPositionState({ duration: NaN, position: 5, playbackRate: 1 })
        applyPositionState({ duration: 0, position: 0, playbackRate: 1 })
        applyPositionState({ duration: Infinity, position: 0, playbackRate: 1 })
        expect(ms.setPositionState).not.toHaveBeenCalled()
    })

    it('clamps position into [0, duration] — the browser throws otherwise', () => {
        applyPositionState({ duration: 100, position: 140, playbackRate: 1 })
        applyPositionState({ duration: 100, position: -3, playbackRate: 1 })
        expect(ms.setPositionState.mock.calls[0][0].position).toBe(100)
        expect(ms.setPositionState.mock.calls[1][0].position).toBe(0)
    })

    it('swallows a browser that rejects the state', () => {
        ms.setPositionState.mockImplementation(() => { throw new TypeError('bad state') })
        expect(() => applyPositionState({ duration: 10, position: 1, playbackRate: 1 })).not.toThrow()
    })
})

describe('bindActionHandlers', () => {
    let ms
    beforeEach(() => { ms = installMediaSession() })

    it('registers every handler it is given and clears the rest', () => {
        const play = vi.fn()
        bindActionHandlers({ play })
        expect(ms.setActionHandler).toHaveBeenCalledWith('play', play)
        for (const action of MEDIA_SESSION_ACTIONS.filter((a) => a !== 'play')) {
            expect(ms.setActionHandler).toHaveBeenCalledWith(action, null)
        }
    })

    it('keeps going when the browser rejects an action it does not support', () => {
        ms.setActionHandler.mockImplementation((action) => {
            if (action === 'seekto') throw new TypeError('unsupported')
        })
        const stop = vi.fn()
        expect(() => bindActionHandlers({ seekto: () => {}, stop })).not.toThrow()
        expect(ms.setActionHandler).toHaveBeenCalledWith('stop', stop)
    })
})

describe('setPlaybackState / clearMediaSession', () => {
    let ms
    beforeEach(() => { ms = installMediaSession() })

    it('sets the playback state', () => {
        setPlaybackState('playing')
        expect(ms.playbackState).toBe('playing')
    })

    it('clear drops metadata, resets state, and unbinds handlers', () => {
        applyMetadata({ title: 'T', artist: 'A', artworkUrl: null })
        setPlaybackState('paused')
        clearMediaSession()
        expect(ms.metadata).toBeNull()
        expect(ms.playbackState).toBe('none')
        for (const action of MEDIA_SESSION_ACTIONS) {
            expect(ms.setActionHandler).toHaveBeenCalledWith(action, null)
        }
    })
})
