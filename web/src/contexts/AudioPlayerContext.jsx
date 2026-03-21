import React, { createContext, useContext, useState, useRef, useCallback, useEffect } from 'react'
import { getAudiobookStreamUrl, updateProgress, getAccessToken } from '../api'

const AudioPlayerContext = createContext(null)

export function useAudioPlayer() {
    return useContext(AudioPlayerContext)
}

export function AudioPlayerProvider({ children }) {
    const audioRef = useRef(null)
    const saveIntervalRef = useRef(null)
    const sleepTimerRef = useRef(null)

    const [currentAudiobook, setCurrentAudiobook] = useState(null) // { id, title, author, coverPath, durationSeconds, pairId }
    const [playing, setPlaying] = useState(false)
    const [currentTime, setCurrentTime] = useState(0)
    const [duration, setDuration] = useState(0)
    const [speed, setSpeedState] = useState(1)
    const [sleepMinutes, setSleepMinutes] = useState(null)

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
            // Mark complete on finish
            if (currentAudiobook) {
                updateProgress('audiobook', currentAudiobook.id, {
                    is_completed: true,
                    device_id: 'web',
                }).catch(() => {})
            }
        }

        audio.addEventListener('play', onPlay)
        audio.addEventListener('pause', onPause)
        audio.addEventListener('timeupdate', onTimeUpdate)
        audio.addEventListener('durationchange', onDurationChange)
        audio.addEventListener('ended', onEnded)

        return () => {
            audio.removeEventListener('play', onPlay)
            audio.removeEventListener('pause', onPause)
            audio.removeEventListener('timeupdate', onTimeUpdate)
            audio.removeEventListener('durationchange', onDurationChange)
            audio.removeEventListener('ended', onEnded)
            audio.pause()
            audio.src = ''
        }
    }, []) // eslint-disable-line react-hooks/exhaustive-deps

    // Auto-save progress every 5s while playing
    useEffect(() => {
        if (saveIntervalRef.current) clearInterval(saveIntervalRef.current)

        if (playing && currentAudiobook) {
            saveIntervalRef.current = setInterval(() => {
                const audio = audioRef.current
                if (audio && currentAudiobook) {
                    updateProgress('audiobook', currentAudiobook.id, {
                        audio_position_ms: Math.floor(audio.currentTime * 1000),
                        device_id: 'web',
                    }).catch(() => {})
                }
            }, 5000)
        }

        return () => {
            if (saveIntervalRef.current) clearInterval(saveIntervalRef.current)
        }
    }, [playing, currentAudiobook])

    const play = useCallback((audiobookId, audiobook, positionMs = 0) => {
        const audio = audioRef.current
        if (!audio) return

        // If same audiobook, just resume
        if (currentAudiobook?.id === audiobookId && audio.src) {
            audio.play()
            return
        }

        // Load new audiobook
        const url = getAudiobookStreamUrl(audiobookId)
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
        // Save position immediately on pause
        if (currentAudiobook && audioRef.current) {
            updateProgress('audiobook', currentAudiobook.id, {
                audio_position_ms: Math.floor(audioRef.current.currentTime * 1000),
                device_id: 'web',
            }).catch(() => {})
        }
    }, [currentAudiobook])

    const togglePlayPause = useCallback(() => {
        if (playing) pause()
        else audioRef.current?.play()
    }, [playing, pause])

    const seekTo = useCallback((seconds) => {
        if (audioRef.current) {
            audioRef.current.currentTime = seconds
        }
    }, [])

    const skipForward = useCallback((seconds = 30) => {
        if (audioRef.current) {
            audioRef.current.currentTime = Math.min(
                audioRef.current.currentTime + seconds,
                audioRef.current.duration || Infinity
            )
        }
    }, [])

    const skipBackward = useCallback((seconds = 15) => {
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
        playing,
        currentTime,
        duration,
        speed,
        sleepMinutes,
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
