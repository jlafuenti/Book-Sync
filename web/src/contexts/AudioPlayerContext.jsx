import React, { createContext, useContext, useState, useRef, useCallback, useEffect } from 'react'
import { getAudiobookStreamUrl, updatePosition, getAccessToken, sendPositionKeepalive, getDeviceId, getDeviceName } from '../api'

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

    const [currentAudiobook, setCurrentAudiobook] = useState(null) // { id, title, author, coverPath, durationSeconds, pairId, pairedEbookId }
    const [pairedEbookId, setPairedEbookId] = useState(null)
    const [playing, setPlaying] = useState(false)
    const [currentTime, setCurrentTime] = useState(0)
    const [duration, setDuration] = useState(0)
    const [speed, setSpeedState] = useState(1)
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

    // Create audio element once
    useEffect(() => {
        audioRef.current = new Audio()
        audioRef.current.preload = 'auto'

        const audio = audioRef.current
        const onPlay = () => setPlaying(true)
        const onPause = () => setPlaying(false)
        const onTimeUpdate = () => setCurrentTime(audio.currentTime)
        const onDurationChange = () => setDuration(audio.duration || 0)
        const onEnded = () => {
            setPlaying(false)
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
                const url = await getAudiobookStreamUrl(ab.id)
                audio.src = url
                audio.load()
                const onCanPlay = () => {
                    // Exact position, no resume rewind: this is a transparent
                    // token refresh, not a user resume (issue #42).
                    audio.currentTime = posSeconds
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

    // Auto-save progress every 5s while playing. Heartbeat keeps the bookmark
    // position fresh so a crash / tab close costs at most a few seconds of
    // listening. The 5s cadence WAS also clogging the history log; now the
    // history entry is gated on `append_to_log`, which only flips true once
    // every 30 min of continuous playback (pauses freeze the timer because
    // this interval stops running when `playing` goes false).
    useEffect(() => {
        if (saveIntervalRef.current) clearInterval(saveIntervalRef.current)

        if (playing && currentAudiobook) {
            // Seed the log timer so the first log entry lands 30 min into the
            // session, not immediately on resume.
            if (lastLogTimeRef.current === 0) {
                lastLogTimeRef.current = Date.now()
            }
            saveIntervalRef.current = setInterval(() => {
                const audio = audioRef.current
                if (audio && currentAudiobook) {
                    // One write per tick; flip append_to_log only when the
                    // 30-min continuous-playback threshold has been crossed.
                    const shouldLog = Date.now() - lastLogTimeRef.current >= LOG_INTERVAL_MS
                    if (shouldLog) lastLogTimeRef.current = Date.now()
                    const [scope, id] = positionTarget(currentAudiobook)
                    updatePosition(scope, id, {
                        source: 'audiobook',
                        audio_position_ms: Math.floor(audio.currentTime * 1000),
                        append_to_log: shouldLog,
                        device_id: getDeviceId(),
                        device_name: getDeviceName(),
                        captured_at: new Date().toISOString(),
                    }).then(handleConflict).catch(() => {})
                }
            }, 5000)
        }

        return () => {
            if (saveIntervalRef.current) clearInterval(saveIntervalRef.current)
        }
    }, [playing, currentAudiobook, handleConflict])

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
    }, [currentAudiobook, speed])

    const pause = useCallback(() => {
        audioRef.current?.pause()
        // Save position immediately on pause. Pause is a session boundary —
        // log a history entry and reset the 30-min continuous-playback timer.
        if (currentAudiobook && audioRef.current) {
            const [scope, id] = positionTarget(currentAudiobook)
            updatePosition(scope, id, {
                source: 'audiobook',
                audio_position_ms: Math.floor(audioRef.current.currentTime * 1000),
                append_to_log: true,
                device_id: getDeviceId(),
                device_name: getDeviceName(),
                captured_at: new Date().toISOString(),
            }).then(handleConflict).catch(() => {})
            lastLogTimeRef.current = Date.now()
        }
    }, [currentAudiobook, handleConflict])

    // The one resume path in this app -- the audio element is a bare `new
    // Audio()` with no controls and no mediaSession handlers, so nothing else
    // can start playback behind our back. Resuming rewinds RESUME_REWIND_SECONDS
    // so you don't restart mid-word (issue #42).
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

    const seekTo = useCallback((seconds) => {
        if (audioRef.current) {
            audioRef.current.currentTime = seconds
        }
    }, [])

    const skipForward = useCallback((seconds = SKIP_SECONDS) => {
        if (audioRef.current) {
            audioRef.current.currentTime = Math.min(
                audioRef.current.currentTime + seconds,
                audioRef.current.duration || Infinity
            )
        }
    }, [])

    const skipBackward = useCallback((seconds = SKIP_SECONDS) => {
        if (audioRef.current) {
            audioRef.current.currentTime = Math.max(audioRef.current.currentTime - seconds, 0)
        }
    }, [])

    const setSpeed = useCallback((rate) => {
        setSpeedState(rate)
        if (audioRef.current) {
            audioRef.current.playbackRate = rate
        }
    }, [])

    const setSleepTimer = useCallback((minutes) => {
        if (sleepTimerRef.current) clearTimeout(sleepTimerRef.current)
        if (!minutes) {
            setSleepMinutes(null)
            return
        }
        setSleepMinutes(minutes)
        sleepTimerRef.current = setTimeout(() => {
            audioRef.current?.pause()
            setSleepMinutes(null)
        }, minutes * 60 * 1000)
    }, [])

    const stop = useCallback(() => {
        if (audioRef.current) {
            audioRef.current.pause()
            audioRef.current.src = ''
        }
        setCurrentAudiobook(null)
        setPlaying(false)
        setCurrentTime(0)
        setDuration(0)
    }, [])

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
