import React, { useState, useEffect, useRef } from 'react'
import { useAudioPlayer } from '../contexts/AudioPlayerContext'
import { getAudiobookChapters, getBookmarkLog, getAccessToken } from '../api'
import './AudioPlayer.css'

function formatTime(seconds) {
    if (!seconds || !isFinite(seconds)) return '0:00'
    const h = Math.floor(seconds / 3600)
    const m = Math.floor((seconds % 3600) / 60)
    const s = Math.floor(seconds % 60)
    if (h > 0) return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
    return `${m}:${s.toString().padStart(2, '0')}`
}

const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 2]
const SLEEP_OPTIONS = [15, 30, 45, 60]

function formatDate(iso) {
    if (!iso) return ''
    const d = new Date(iso)
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) + ' ' +
        d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
}

// ---- Full Player View ----
export function AudioPlayerView({ onClose, onSwitchToEbook }) {
    const player = useAudioPlayer()
    const [chapters, setChapters] = useState([])
    const [showSleepMenu, setShowSleepMenu] = useState(false)
    const [showChapters, setShowChapters] = useState(true)
    const [showHistory, setShowHistory] = useState(false)
    const [historyLog, setHistoryLog] = useState([])

    useEffect(() => {
        if (player.currentAudiobook?.id) {
            getAudiobookChapters(player.currentAudiobook.id)
                .then(setChapters)
                .catch(() => setChapters([]))
        }
    }, [player.currentAudiobook?.id])

    useEffect(() => {
        if (showHistory && player.currentAudiobook?.pairId) {
            getBookmarkLog(player.currentAudiobook.pairId)
                .then(setHistoryLog)
                .catch(() => setHistoryLog([]))
        }
    }, [showHistory, player.currentAudiobook?.pairId])

    if (!player.currentAudiobook) return null

    const { currentAudiobook, pairedEbookId, playing, currentTime, duration, speed, sleepMinutes } = player
    const progressPercent = duration > 0 ? (currentTime / duration) * 100 : 0

    const currentChapter = chapters.length > 0
        ? [...chapters].reverse().find(ch => currentTime >= (ch.start_time || ch.startTime || 0))
        : null

    const coverUrl = currentAudiobook.coverPath
        ? `${currentAudiobook.coverPath}?token=${getAccessToken()}`
        : null

    return (
        <div className="audio-player-overlay">
            <div className="audio-player-toolbar">
                <button className="btn-icon" onClick={onClose} title="Close player">
                    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2">
                        <polyline points="15 18 9 12 15 6" />
                    </svg>
                </button>
                <span className="audio-player-title">Now Playing</span>
                {onSwitchToEbook && currentAudiobook.pairId && (
                    <button
                        className="btn-icon switch-format-btn"
                        onClick={() => onSwitchToEbook(currentAudiobook.pairId, pairedEbookId)}
                        title="Switch to Ebook"
                    >
                        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2">
                            <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
                            <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
                        </svg>
                        <span style={{ fontSize: 12, marginLeft: 4 }}>Read</span>
                    </button>
                )}
                <button className="btn-icon" onClick={() => setShowChapters(!showChapters)} title="Toggle chapters">
                    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2">
                        <line x1="3" y1="6" x2="21" y2="6" /><line x1="3" y1="12" x2="21" y2="12" /><line x1="3" y1="18" x2="21" y2="18" />
                    </svg>
                </button>
                {currentAudiobook.pairId && (
                    <button className={`btn-icon${showHistory ? ' active' : ''}`} onClick={() => { setShowHistory(!showHistory); setShowChapters(false) }} title="Session history">
                        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2">
                            <circle cx="12" cy="12" r="10" />
                            <polyline points="12 6 12 12 16 14" />
                        </svg>
                    </button>
                )}
            </div>

            <div className="audio-player-body">
                <div className="audio-player-main">
                    {/* Cover art */}
                    <div className="audio-player-cover">
                        {coverUrl ? (
                            <img src={coverUrl} alt={currentAudiobook.title} />
                        ) : (
                            <span className="cover-placeholder">🎧</span>
                        )}
                    </div>

                    {/* Title / author */}
                    <div className="audio-player-info">
                        <h2>{currentAudiobook.title}</h2>
                        {currentAudiobook.author && <p>{currentAudiobook.author}</p>}
                        {currentChapter && (
                            <p style={{ marginTop: 4, fontSize: 13, color: 'var(--text-muted)' }}>
                                {currentChapter.title}
                            </p>
                        )}
                    </div>

                    {/* Seek bar */}
                    <div className="audio-seek-container">
                        <input
                            type="range"
                            className="audio-seek-bar"
                            min={0}
                            max={duration || 0}
                            step={1}
                            value={currentTime}
                            onChange={e => player.seekTo(Number(e.target.value))}
                            style={{
                                background: `linear-gradient(to right, var(--accent) ${progressPercent}%, var(--border) ${progressPercent}%)`
                            }}
                        />
                        <div className="audio-seek-times">
                            <span>{formatTime(currentTime)}</span>
                            <span>-{formatTime(duration - currentTime)}</span>
                        </div>
                    </div>

                    {/* Transport controls */}
                    <div className="audio-transport">
                        <button onClick={() => player.skipBackward(15)} title="Back 15s">
                            <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" strokeWidth="2">
                                <path d="M12.5 8V4l-5 4 5 4V8" /><path d="M19 12a7 7 0 1 1-7-7" />
                            </svg>
                        </button>
                        <button className="play-btn" onClick={player.togglePlayPause}>
                            {playing ? (
                                <svg viewBox="0 0 24 24" width="28" height="28" fill="currentColor">
                                    <rect x="6" y="4" width="4" height="16" /><rect x="14" y="4" width="4" height="16" />
                                </svg>
                            ) : (
                                <svg viewBox="0 0 24 24" width="28" height="28" fill="currentColor">
                                    <polygon points="6,4 20,12 6,20" />
                                </svg>
                            )}
                        </button>
                        <button onClick={() => player.skipForward(30)} title="Forward 30s">
                            <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" strokeWidth="2">
                                <path d="M11.5 8V4l5 4-5 4V8" /><path d="M5 12a7 7 0 1 0 7-7" />
                            </svg>
                        </button>
                    </div>

                    {/* Speed + sleep */}
                    <div className="audio-secondary-controls">
                        <div className="speed-selector">
                            {SPEEDS.map(s => (
                                <button
                                    key={s}
                                    className={`speed-btn${speed === s ? ' active' : ''}`}
                                    onClick={() => player.setSpeed(s)}
                                >
                                    {s}x
                                </button>
                            ))}
                        </div>
                        <div style={{ position: 'relative' }}>
                            <button
                                className={`sleep-timer-btn${sleepMinutes ? ' active' : ''}`}
                                onClick={() => setShowSleepMenu(!showSleepMenu)}
                            >
                                {sleepMinutes ? `${sleepMinutes}m` : 'Sleep'}
                            </button>
                            {showSleepMenu && (
                                <div className="sleep-timer-dropdown">
                                    {SLEEP_OPTIONS.map(m => (
                                        <button key={m} onClick={() => { player.setSleepTimer(m); setShowSleepMenu(false) }}>
                                            {m} min
                                        </button>
                                    ))}
                                    {sleepMinutes && (
                                        <button onClick={() => { player.setSleepTimer(null); setShowSleepMenu(false) }}>
                                            Cancel
                                        </button>
                                    )}
                                </div>
                            )}
                        </div>
                    </div>
                </div>

                {/* History panel */}
                {showHistory && (
                    <div className="audio-chapters-panel">
                        <div className="audio-chapters-header">
                            <span>Session History</span>
                            <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>{historyLog.length}</span>
                        </div>
                        <div className="audio-chapters-list">
                            {historyLog.length === 0 ? (
                                <div style={{ padding: '16px', color: 'var(--text-muted)', fontSize: 13, textAlign: 'center' }}>
                                    No history yet
                                </div>
                            ) : historyLog.map((entry, i) => (
                                <button
                                    key={i}
                                    className="audio-chapter-item"
                                    onClick={() => player.seekTo((entry.new_audio_position_ms || 0) / 1000)}
                                >
                                    <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                        {formatDate(entry.changed_at)}
                                    </span>
                                    <span className="audio-chapter-time">{formatTime((entry.new_audio_position_ms || 0) / 1000)}</span>
                                </button>
                            ))}
                        </div>
                    </div>
                )}

                {/* Chapter list */}
                {showChapters && chapters.length > 0 && (
                    <div className="audio-chapters-panel">
                        <div className="audio-chapters-header">
                            <span>Chapters</span>
                            <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>{chapters.length}</span>
                        </div>
                        <div className="audio-chapters-list">
                            {chapters.map((ch, i) => {
                                const startSec = ch.start_time || ch.startTime || 0
                                const isActive = currentChapter &&
                                    (ch.title === currentChapter.title) &&
                                    (startSec === (currentChapter.start_time || currentChapter.startTime || 0))
                                return (
                                    <button
                                        key={i}
                                        className={`audio-chapter-item${isActive ? ' active' : ''}`}
                                        onClick={() => player.seekTo(startSec)}
                                    >
                                        <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                            {ch.title}
                                        </span>
                                        <span className="audio-chapter-time">{formatTime(startSec)}</span>
                                    </button>
                                )
                            })}
                        </div>
                    </div>
                )}
            </div>
        </div>
    )
}

// ---- Mini Player Bar ----
export function MiniPlayer({ onExpand }) {
    const player = useAudioPlayer()

    if (!player.currentAudiobook) return null

    const { currentAudiobook, playing, currentTime, duration } = player
    const progressPercent = duration > 0 ? (currentTime / duration) * 100 : 0

    const coverUrl = currentAudiobook.coverPath
        ? `${currentAudiobook.coverPath}?token=${getAccessToken()}`
        : null

    return (
        <div className="mini-player" onClick={onExpand} style={{ cursor: 'pointer' }}>
            <div className="mini-player-progress">
                <div className="mini-player-progress-fill" style={{ width: `${progressPercent}%` }} />
            </div>
            <div className="mini-player-cover">
                {coverUrl ? (
                    <img src={coverUrl} alt="" />
                ) : (
                    <span style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: '100%', height: '100%', fontSize: 20 }}>🎧</span>
                )}
            </div>
            <div className="mini-player-info">
                <div className="title">{currentAudiobook.title}</div>
                <div className="author">{currentAudiobook.author}</div>
            </div>
            <span className="mini-player-time">{formatTime(currentTime)}</span>
            <div className="mini-player-controls" onClick={e => e.stopPropagation()}>
                <button onClick={() => player.skipBackward(15)} title="Back 15s">
                    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2">
                        <path d="M12.5 8V4l-5 4 5 4V8" /><path d="M19 12a7 7 0 1 1-7-7" />
                    </svg>
                </button>
                <button onClick={player.togglePlayPause}>
                    {playing ? (
                        <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
                            <rect x="6" y="4" width="4" height="16" /><rect x="14" y="4" width="4" height="16" />
                        </svg>
                    ) : (
                        <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
                            <polygon points="6,4 20,12 6,20" />
                        </svg>
                    )}
                </button>
                <button onClick={() => player.skipForward(30)} title="Forward 30s">
                    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2">
                        <path d="M11.5 8V4l5 4-5 4V8" /><path d="M5 12a7 7 0 1 0 7-7" />
                    </svg>
                </button>
                <button onClick={player.stop} title="Stop">
                    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2">
                        <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
                    </svg>
                </button>
            </div>
        </div>
    )
}
