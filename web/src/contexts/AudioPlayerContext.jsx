import React, { createContext, useContext, useState, useRef, useCallback, useEffect } from 'react'
import { getAudiobookStreamUrl, updatePosition, sendPositionKeepalive, getDeviceId, getDeviceName, coverSrc } from '../api'
import {
    applyMetadata, applyPositionState, setPlaybackState, bindActionHandlers, clearMediaSession,
} from '../lib/mediaSession'

const AudioPlayerContext = createContext(null)

// Where a player save goes. A paired audiobook shares one record with its
// ebook, so the reader and the player can't drift apart; an unpaired one gets
// its own standalone record.
function positionTarget(audiobook) {
    return audiobook?.pairId ? ['pair', audiobook.pairId] : ['audiobook', audiobook.id]
}

// Write a history-log entry every 30 min of continuous playback (in addition
// to pause / stop / ended boundaries). Keeps the Session History panel clean —
// a 1-hour uninterrupted session logs ~2 entries instead of ~720.
const LOG_INTERVAL_MS = 30 * 60 * 1000

// Position-save cadence (issue #65). The heartbeat ticks every 5s, but a
// network push only goes out once NETWORK_SAVE_INTERVAL_MS has elapsed since
// the last successful one — recording "position advanced 5s" as a Postgres
// UPDATE 720 times an hour per device bought nothing, since the server copy
// only matters for cross-device resume, where 30s of staleness is
// imperceptible. Session boundaries (pause, seek, sleep-timer stop, book
// change, stop, unload) flush immediately regardless. Android's
// AudioPlayerService keeps the same two numbers; see
// docs/position-sync-contract.md § Save cadence before changing either.
const HEARTBEAT_TICK_MS = 5000
const NETWORK_SAVE_INTERVAL_MS = 30_000
// A slider scrub fires many seeks; coalesce them into one write.
const SEEK_FLUSH_DEBOUNCE_MS = 1000

// Playback offsets (issue #42). These two numbers are the web copy of a value
// that must be identical on every surface -- Android's PlaybackOffsets and the
// server's default_rewind_seconds carry the same ones. See
// docs/position-sync-contract.md § Playback offsets before changing either.
//
// SKIP: both transport buttons, same in each direction.
// RESUME_REWIND: picking up mid-word after a pause is hard to follow, so a
// resume backs up a few seconds first. Also the size of the text->audio
// handoff jump on Android.
const SKIP_SECONDS = 30
const RESUME_REWIND_SECONDS = 5

// How often the Media Session scrubber is updated from `timeupdate` (issue
// #62). The OS interpolates between updates, so ~1 s is plenty.
const POSITION_STATE_INTERVAL_MS = 1000

// Playback speed persists across sessions (issue #57). Kept in localStorage —
// device-local, like Android's SharedPreferences equivalent.
const SPEED_STORAGE_KEY = 'tandem_player_speed'
const MIN_SPEED = 0.5
const MAX_SPEED = 3

function loadStoredSpeed() {
    const stored = parseFloat(localStorage.getItem(SPEED_STORAGE_KEY))
    if (Number.isFinite(stored) && stored >= MIN_SPEED && stored <= MAX_SPEED) {
        return stored
    }
    return 1
}

export function useAudioPlayer() {
    return useContext(AudioPlayerContext)
}

export function AudioPlayerProvider({ children }) {
    const audioRef = useRef(null)
    const saveIntervalRef = useRef(null)
    const sleepTimerRef = useRef(null)
    // Last time we wrote a history-log entry (append_to_log=true). Updated on
    // pause, ended, 30-min tick, and beforeunload. Only advanced while playing,
    // so pauses freeze the 30-min clock.
    const lastLogTimeRef = useRef(0)
    // Latest audiobook / timing refs so the beforeunload handler can read
    // current state without re-binding the listener on every state change.
    const currentAudiobookRef = useRef(null)
    // Live "was playing at this instant" snapshot for the unload handler
    // (product rule: a save only claims `source` when playing or triggered
    // by an explicit user command — see `onUnload` below). A plain `playing`
    // state read would be stale here since this ref, like currentAudiobookRef,
    // exists so the mount-only unload listener can see current state without
    // re-binding on every play/pause.
    const playingRef = useRef(false)
    // Guards the stream-error recovery below against retry loops: only one
    // re-mint attempt per load, cleared once playback resumes successfully.
    const recoveringStreamRef = useRef(false)
    // Network-push throttle (issue #65): when the last push *succeeded*, and
    // whether one is still in flight. A failed push leaves lastPushTimeRef
    // alone so the next tick retries instead of waiting out another 30s.
    const lastPushTimeRef = useRef(0)
    const pushInFlightRef = useRef(false)
    const seekFlushTimerRef = useRef(null)
    // Latest flushPosition, readable from timer callbacks (sleep timer, seek
    // debounce) that would otherwise close over a stale one.
    const flushRef = useRef(() => Promise.resolve())
    // Last time the OS scrubber was fed (Media Session position state).
    const lastPositionStateRef = useRef(0)

    const [currentAudiobook, setCurrentAudiobook] = useState(null) // { id, title, author, coverPath, durationSeconds, pairId, pairedEbookId }
    const [pairedEbookId, setPairedEbookId] = useState(null)
    const [playing, setPlaying] = useState(false)
    const [currentTime, setCurrentTime] = useState(0)
    const [duration, setDuration] = useState(0)
    const [speed, setSpeedState] = useState(loadStoredSpeed)
    const [sleepMinutes, setSleepMinutes] = useState(null)
    // Set when a bookmark/progress write comes back `rejected: true` (issue
    // #54 stale-write conflict) carrying a newer position from a genuinely
    // *different* device. { position (seconds), deviceName }. Never set (and
    // never auto-seeked) for a rejection that just echoes this device's own
    // id -- e.g. this device's retried/out-of-order write bouncing off itself.
    const [staleConflict, setStaleConflict] = useState(null)

    // Attach to every updatePosition call as `.then(handleConflict)`.
    // Passes the result through unchanged so it stays chainable.
    const handleConflict = useCallback((result) => {
        if (result && result.rejected && result.device_id && result.device_id !== getDeviceId()) {
            setStaleConflict({
                position: (result.audio_position_ms || 0) / 1000,
                deviceName: result.device_name || result.device_id,
            })
        }
        return result
    }, [])

    const clearStaleConflict = useCallback(() => setStaleConflict(null), [])

    // The one position write for the player (issue #65). Boundary flushes call
    // it directly; the heartbeat calls it only when the throttle says so.
    //
    // `appendToLog` marks a session boundary (history entry + resets the
    // 30-min continuous-playback clock). `claimFormat` follows the product
    // rule from `onUnload`: only a playing player or an explicit user command
    // claims `source`. `target` lets a book-change flush name the book being
    // left after state has already moved on.
    const flushPosition = useCallback(({ appendToLog = false, claimFormat = true, target = null } = {}) => {
        const audio = audioRef.current
        const ab = target || currentAudiobookRef.current
        if (!audio || !ab) return Promise.resolve()
        if (appendToLog) lastLogTimeRef.current = Date.now()
        const [scope, id] = positionTarget(ab)
        pushInFlightRef.current = true
        return updatePosition(scope, id, {
            source: claimFormat ? 'audiobook' : undefined,
            audio_position_ms: Math.floor(audio.currentTime * 1000),
            append_to_log: appendToLog,
            device_id: getDeviceId(),
            device_name: getDeviceName(),
            captured_at: new Date().toISOString(),
        }).then((result) => {
            lastPushTimeRef.current = Date.now()
            return handleConflict(result)
        }).catch(() => {}).finally(() => {
            pushInFlightRef.current = false
        })
    }, [handleConflict])

    useEffect(() => {
        flushRef.current = flushPosition
    }, [flushPosition])

    // Seek / speed changes: one write after the last change settles, so a
    // slider scrub doesn't turn into a burst of PUTs.
    const scheduleFlush = useCallback(() => {
        if (!currentAudiobookRef.current) return
        if (seekFlushTimerRef.current) clearTimeout(seekFlushTimerRef.current)
        seekFlushTimerRef.current = setTimeout(() => {
            seekFlushTimerRef.current = null
            flushRef.current({ appendToLog: false, claimFormat: playingRef.current })
        }, SEEK_FLUSH_DEBOUNCE_MS)
    }, [])

    // Create audio element once
    useEffect(() => {
        audioRef.current = new Audio()
        audioRef.current.preload = 'auto'
        // Apply the restored speed to the element from the start; play() also
        // re-applies it on every src swap.
        audioRef.current.playbackRate = loadStoredSpeed()

        const audio = audioRef.current
        // Media Session (issue #62): mirror element state to the OS so the
        // lock-screen / notification controls and scrubber stay truthful.
        // Position state is throttled — timeupdate fires ~4×/s and the OS
        // scrubber interpolates between updates anyway.
        const positionState = () => applyPositionState({
            duration: audio.duration, position: audio.currentTime, playbackRate: audio.playbackRate,
        })
        const onPlay = () => { setPlaying(true); setPlaybackState('playing') }
        const onPause = () => { setPlaying(false); setPlaybackState('paused') }
        const onTimeUpdate = () => {
            setCurrentTime(audio.currentTime)
            const now = Date.now()
            if (now - lastPositionStateRef.current >= POSITION_STATE_INTERVAL_MS) {
                lastPositionStateRef.current = now
                positionState()
            }
        }
        const onDurationChange = () => { setDuration(audio.duration || 0); positionState() }
        const onEnded = () => {
            setPlaying(false)
            setPlaybackState('paused')
            // Mark complete on finish. Reads currentAudiobookRef (not the
            // `currentAudiobook` state closed over by this mount-only effect,
            // which is permanently null) -- same fix as onError below, which
            // already uses the ref for the same reason.
            const ab = currentAudiobookRef.current
            if (ab) {
                // ONE write: completion, final position, and the "finished"
                // history entry travel together. As two writes they were
                // adjudicated separately, so the completion flag could land
                // while the position was rejected as stale (or vice versa).
                const [scope, id] = positionTarget(ab)
                updatePosition(scope, id, {
                    source: 'audiobook',
                    is_completed: true,
                    audio_position_ms: audio ? Math.floor(audio.currentTime * 1000) : undefined,
                    append_to_log: true,
                    device_id: getDeviceId(),
                    device_name: getDeviceName(),
                    captured_at: new Date().toISOString(),
                }).then(handleConflict).catch(() => {})
                lastLogTimeRef.current = Date.now()
            }
        }

        // The media token embedded in audio.src is short-lived (15 min, see
        // issue #50). A long pause/seek session can outlive it, causing the
        // browser to fail a byte-range (re)request with a 401. Re-mint a
        // fresh scoped URL and resume from the same position -- once per
        // error, to avoid retry loops on genuine playback failures.
        const onError = async () => {
            const ab = currentAudiobookRef.current
            if (!ab || recoveringStreamRef.current) return
            recoveringStreamRef.current = true
            try {
                const wasPlaying = !audio.paused
                const posSeconds = audio.currentTime
                const rate = audio.playbackRate
                const url = await getAudiobookStreamUrl(ab.id)
                audio.src = url
                audio.load()
                const onCanPlay = () => {
                    // Exact position, no resume rewind: this is a transparent
                    // token refresh, not a user resume (issue #42). Same for
                    // speed — load() may reset playbackRate to default.
                    audio.currentTime = posSeconds
                    audio.playbackRate = rate
                    if (wasPlaying) audio.play()
                    audio.removeEventListener('canplay', onCanPlay)
                    recoveringStreamRef.current = false
                }
                audio.addEventListener('canplay', onCanPlay)
            } catch {
                recoveringStreamRef.current = false
            }
        }

        audio.addEventListener('play', onPlay)
        audio.addEventListener('pause', onPause)
        audio.addEventListener('timeupdate', onTimeUpdate)
        audio.addEventListener('durationchange', onDurationChange)
        audio.addEventListener('ended', onEnded)
        audio.addEventListener('error', onError)

        return () => {
            audio.removeEventListener('play', onPlay)
            audio.removeEventListener('pause', onPause)
            audio.removeEventListener('timeupdate', onTimeUpdate)
            audio.removeEventListener('durationchange', onDurationChange)
            audio.removeEventListener('ended', onEnded)
            audio.removeEventListener('error', onError)
            audio.pause()
            audio.src = ''
        }
    }, []) // eslint-disable-line react-hooks/exhaustive-deps

    // Keep the latest audiobook in a ref so the beforeunload handler can read
    // it without having to re-bind every time it changes.
    useEffect(() => {
        currentAudiobookRef.current = currentAudiobook
    }, [currentAudiobook])

    useEffect(() => {
        playingRef.current = playing
    }, [playing])

    // Media Session metadata (issue #62): what the lock screen shows. The
    // cover goes through coverSrc() — the same short-lived scoped media token
    // every other <img> uses (issue #50) — so it resolves asynchronously;
    // publish title/author at once and add the artwork when the token lands.
    useEffect(() => {
        if (!currentAudiobook) return
        let cancelled = false
        const base = { title: currentAudiobook.title, artist: currentAudiobook.author, artworkUrl: null }
        applyMetadata(base)
        if (currentAudiobook.coverPath) {
            coverSrc(currentAudiobook.coverPath)
                .then((url) => { if (!cancelled && url) applyMetadata({ ...base, artworkUrl: url }) })
                .catch(() => {})
        }
        return () => { cancelled = true }
    }, [currentAudiobook])

    // Heartbeat while playing (issue #65): ticks every 5s, but only pushes to
    // the server once NETWORK_SAVE_INTERVAL_MS has passed since the last
    // successful push. The unload keepalive covers a tab close; a browser
    // crash costs at most 30s, which is what the server copy is for anyway
    // (cross-device resume). The history entry is gated separately on
    // `append_to_log`, which flips true once every 30 min of continuous
    // playback (pauses freeze both clocks because this interval stops running
    // when `playing` goes false).
    useEffect(() => {
        if (saveIntervalRef.current) clearInterval(saveIntervalRef.current)

        if (playing && currentAudiobook) {
            // Seed both timers so the first log entry lands 30 min into the
            // session and the first push 30s in — not immediately on resume,
            // which just flushed on the pause boundary.
            if (lastLogTimeRef.current === 0) {
                lastLogTimeRef.current = Date.now()
            }
            lastPushTimeRef.current = Date.now()
            saveIntervalRef.current = setInterval(() => {
                const now = Date.now()
                const shouldLog = now - lastLogTimeRef.current >= LOG_INTERVAL_MS
                const pushDue = now - lastPushTimeRef.current >= NETWORK_SAVE_INTERVAL_MS
                if (shouldLog || (pushDue && !pushInFlightRef.current)) {
                    flushPosition({ appendToLog: shouldLog, claimFormat: true })
                }
            }, HEARTBEAT_TICK_MS)
        }

        return () => {
            if (saveIntervalRef.current) clearInterval(saveIntervalRef.current)
        }
    }, [playing, currentAudiobook, flushPosition])

    // Final history entry when the tab closes / reloads. Regular fetch is
    // aborted during unload, so we use the keepalive helper that sends the
    // request in the background. sendBeacon can't be used here because the
    // canonical position endpoint is PUT, not POST.
    //
    // claimFormat = was actually playing at this instant (product rule: a
    // save only claims `source` when playing, or triggered by an explicit
    // user command — this teardown is neither when the player is paused/idle
    // in the background). `PositionUpdate.source` is optional precisely so
    // this save can move the position without re-claiming the format.
    useEffect(() => {
        const onUnload = () => {
            const audio = audioRef.current
            const ab = currentAudiobookRef.current
            if (!ab || !audio) return
            const claimFormat = playingRef.current
            const [scope, id] = positionTarget(ab)
            sendPositionKeepalive(scope, id, {
                source: claimFormat ? 'audiobook' : undefined,
                audio_position_ms: Math.floor(audio.currentTime * 1000),
                append_to_log: true,
                device_id: getDeviceId(),
                device_name: getDeviceName(),
                captured_at: new Date().toISOString(),
            })
        }
        // pagehide fires more reliably than beforeunload on mobile Safari.
        window.addEventListener('pagehide', onUnload)
        window.addEventListener('beforeunload', onUnload)
        return () => {
            window.removeEventListener('pagehide', onUnload)
            window.removeEventListener('beforeunload', onUnload)
        }
    }, [])

    const play = useCallback(async (audiobookId, audiobook, positionMs = 0, pairedEbookIdArg = null) => {
        setPairedEbookId(pairedEbookIdArg)
        const audio = audioRef.current
        if (!audio) return

        // If same audiobook, seek to requested position (if any) then resume
        if (currentAudiobook?.id === audiobookId && audio.src) {
            if (positionMs > 0) {
                audio.currentTime = positionMs / 1000
            }
            audio.play()
            return
        }

        // Leaving one book for another is a session boundary for the book
        // being left: flush it before the src swap moves the clock.
        if (currentAudiobookRef.current && currentAudiobookRef.current.id !== audiobookId) {
            flushPosition({
                appendToLog: true, claimFormat: playingRef.current,
                target: currentAudiobookRef.current,
            })
        }

        // Load new audiobook
        const url = await getAudiobookStreamUrl(audiobookId)
        audio.src = url
        audio.playbackRate = speed

        const info = {
            id: audiobookId,
            title: audiobook?.title || 'Audiobook',
            author: audiobook?.author || '',
            coverPath: audiobook?.cover_path || audiobook?.coverPath || null,
            durationSeconds: audiobook?.duration_seconds || audiobook?.durationSeconds || 0,
            pairId: audiobook?.pair_id || audiobook?.pairId || null,
        }
        setCurrentAudiobook(info)

        const onCanPlay = () => {
            if (positionMs > 0) {
                audio.currentTime = positionMs / 1000
            }
            audio.play()
            audio.removeEventListener('canplay', onCanPlay)
        }
        audio.addEventListener('canplay', onCanPlay)
        audio.load()
    }, [currentAudiobook, speed, flushPosition])

    const pause = useCallback(() => {
        audioRef.current?.pause()
        // Save position immediately on pause. Pause is a session boundary —
        // log a history entry and reset the 30-min continuous-playback timer.
        // An explicit user command, so it claims the format.
        flushPosition({ appendToLog: true, claimFormat: true })
    }, [flushPosition])

    // The one resume path in this app -- the audio element is a bare `new
    // Audio()` with no controls, and the Media Session `play` action (lock
    // screen, headset, media keys) is routed here too, so nothing can start
    // playback behind our back. Resuming rewinds RESUME_REWIND_SECONDS so you
    // don't restart mid-word (issue #42).
    //
    // Deliberately NOT applied at the other two play() sites: `play()` below
    // carries an explicit position from Home/Continue, and the stream-error
    // recovery restores the exact position it was interrupted at.
    const togglePlayPause = useCallback(() => {
        if (playing) {
            pause()
            return
        }
        const audio = audioRef.current
        if (!audio) return
        audio.currentTime = Math.max(audio.currentTime - RESUME_REWIND_SECONDS, 0)
        audio.play()
    }, [playing, pause])

    // Seeks and skips are boundaries too — the position jumped, so the
    // server copy should follow promptly rather than in up to 30s — but a
    // slider scrub is many seeks, hence the debounce.
    // A jump or a speed change should reach the OS scrubber at once, not on
    // the next throttled timeupdate.
    const pushPositionState = useCallback(() => {
        const audio = audioRef.current
        if (!audio) return
        lastPositionStateRef.current = Date.now()
        applyPositionState({ duration: audio.duration, position: audio.currentTime, playbackRate: audio.playbackRate })
    }, [])

    const seekTo = useCallback((seconds) => {
        if (audioRef.current) {
            audioRef.current.currentTime = seconds
            pushPositionState()
            scheduleFlush()
        }
    }, [scheduleFlush, pushPositionState])

    const skipForward = useCallback((seconds = SKIP_SECONDS) => {
        if (audioRef.current) {
            audioRef.current.currentTime = Math.min(
                audioRef.current.currentTime + seconds,
                audioRef.current.duration || Infinity
            )
            pushPositionState()
            scheduleFlush()
        }
    }, [scheduleFlush, pushPositionState])

    const skipBackward = useCallback((seconds = SKIP_SECONDS) => {
        if (audioRef.current) {
            audioRef.current.currentTime = Math.max(audioRef.current.currentTime - seconds, 0)
            pushPositionState()
            scheduleFlush()
        }
    }, [scheduleFlush, pushPositionState])

    const setSpeed = useCallback((rate) => {
        setSpeedState(rate)
        localStorage.setItem(SPEED_STORAGE_KEY, String(rate))
        if (audioRef.current) {
            audioRef.current.playbackRate = rate
            pushPositionState()
            scheduleFlush()
        }
    }, [scheduleFlush, pushPositionState])

    const setSleepTimer = useCallback((minutes) => {
        if (sleepTimerRef.current) clearTimeout(sleepTimerRef.current)
        if (!minutes) {
            setSleepMinutes(null)
            return
        }
        setSleepMinutes(minutes)
        sleepTimerRef.current = setTimeout(() => {
            // A sleep-timer stop is a session boundary like a pause: flush now
            // rather than leaving the last position to the unload keepalive.
            // The player was playing until this instant, so it claims the format.
            audioRef.current?.pause()
            flushRef.current({ appendToLog: true, claimFormat: true })
            setSleepMinutes(null)
        }, minutes * 60 * 1000)
    }, [])

    const stop = useCallback(() => {
        if (audioRef.current) {
            // Flush before the src swap resets the clock.
            flushPosition({ appendToLog: true, claimFormat: playingRef.current })
            audioRef.current.pause()
            audioRef.current.src = ''
        }
        setCurrentAudiobook(null)
        setPlaying(false)
        setCurrentTime(0)
        setDuration(0)
    }, [flushPosition])

    // Media Session actions (issue #62): the OS controls call the *same*
    // functions as the on-screen transport, so a lock-screen resume gets the
    // 5 s rewind, a lock-screen skip is the contract's 30 s, and every one of
    // them is an explicit user command that claims `source` the way the
    // buttons do. With no book loaded the session is cleared so the OS drops
    // the controls (and metadata) rather than showing a dead player.
    useEffect(() => {
        if (!currentAudiobook) {
            clearMediaSession()
            return
        }
        bindActionHandlers({
            play: () => { if (!playingRef.current) togglePlayPause() },
            pause: () => { if (playingRef.current) pause() },
            stop: () => stop(),
            seekbackward: () => skipBackward(SKIP_SECONDS),
            seekforward: () => skipForward(SKIP_SECONDS),
            seekto: (details) => {
                if (details && Number.isFinite(details.seekTime)) seekTo(details.seekTime)
            },
        })
    }, [currentAudiobook, togglePlayPause, pause, stop, seekTo, skipForward, skipBackward])

    const value = {
        currentAudiobook,
        pairedEbookId,
        playing,
        currentTime,
        duration,
        speed,
        sleepMinutes,
        staleConflict,
        clearStaleConflict,
        play,
        pause,
        togglePlayPause,
        seekTo,
        skipForward,
        skipBackward,
        setSpeed,
        setSleepTimer,
        stop,
    }

    return (
        <AudioPlayerContext.Provider value={value}>
            {children}
        </AudioPlayerContext.Provider>
    )
}
