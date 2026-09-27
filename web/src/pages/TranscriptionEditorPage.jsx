import React, { useState, useEffect, useMemo, useCallback } from 'react'
import { useParams, Link } from 'react-router-dom'
import { getSyncMap, getPair, realignPair } from '../api'
import { useAuth } from '../contexts/AuthContext'
import './TranscriptionPage.css'

/**
 * How a pair's ebook sentences line up with its audio: for each sentence, the
 * audio time range, what the transcript heard there, and whether the aligner
 * matched it or filled it in between matches.
 *
 * Read-only since issue #713. The page used to let you edit the heard text, but
 * that text is display-only: re-alignment rebuilds the map from the saved
 * transcript, so an edit never changed where either app lands. Re-align here
 * is the rebuild - cheap, and no re-transcription.
 */
function TranscriptionEditorPage() {
    const { pairId } = useParams()
    const { hasMinRole } = useAuth()
    const canEdit = hasMinRole('editor')
    const [points, setPoints] = useState([])
    const [pair, setPair] = useState(null)
    const [loading, setLoading] = useState(true)
    const [realigning, setRealigning] = useState(false)
    const [error, setError] = useState('')
    const [successMessage, setSuccessMessage] = useState('')
    const [query, setQuery] = useState('')

    const loadPoints = useCallback(async () => {
        const data = await getSyncMap(pairId)
        setPoints((data.sync_points || []).map(pt => ({
            id: pt.id,
            ebookText: pt.epub_text_preview || '',
            heardText: pt.audio_text || '',
            start_ms: pt.audio_start_ms,
            end_ms: pt.audio_end_ms,
            chapter: pt.epub_chapter,
            sentence: pt.epub_sentence_index,
            matched: pt.confidence > 0,
        })))
    }, [pairId])

    useEffect(() => {
        const load = async () => {
            try {
                await loadPoints()
            } catch (err) {
                setError(err.message)
            } finally {
                setLoading(false)
            }

            // The pair is only the subheading's title/author (issue #277). It
            // used to come from `getPairs()` — the whole library, filtered
            // client-side to one row. Fetch the one, and keep its failure off
            // the page: a missing pair must not blank the points.
            try {
                setPair(await getPair(pairId))
            } catch {
                // Subheading stays empty; the sync map is what matters here.
            }
        }
        load()
    }, [pairId, loadPoints])

    const handleRealign = async () => {
        setRealigning(true)
        setError('')
        setSuccessMessage('')
        try {
            const r = await realignPair(pairId)
            await loadPoints()
            setSuccessMessage(`Re-aligned: ${r.points} sentences, ${r.matched} matched.`)
        } catch (err) {
            setError(err.message)
        } finally {
            setRealigning(false)
        }
    }

    const matchedCount = useMemo(() => points.filter(pt => pt.matched).length, [points])
    const shown = useMemo(() => {
        const q = query.trim().toLowerCase()
        if (!q) return points
        return points.filter(pt => pt.ebookText.toLowerCase().includes(q) || pt.heardText.toLowerCase().includes(q))
    }, [points, query])

    const formatTime = (ms) => {
        const totalSec = Math.floor(ms / 1000)
        const m = Math.floor(totalSec / 60)
        const s = totalSec % 60
        return `${m}:${s.toString().padStart(2, '0')}`
    }

    if (loading) {
        return <div className="loading-page"><div className="spinner"></div> Loading transcript...</div>
    }

    return (
        <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '4px' }}>
                <Link to="/transcription/transcribed" className="btn btn-secondary btn-sm" style={{ textDecoration: 'none' }}>
                    ← Back
                </Link>
                <h2 style={{ margin: 0, fontSize: '1.2rem', fontWeight: 700 }}>Transcript Alignment</h2>
            </div>
            {pair && (
                <p style={{ margin: '0 0 16px 0', color: 'var(--text-secondary)', fontSize: '0.9rem' }}>
                    {pair.ebook.title}{pair.ebook.author && ` by ${pair.ebook.author}`}
                </p>
            )}

            {error && <div className="alert alert-error" style={{ marginBottom: '12px' }}>⚠️ {error}</div>}
            {successMessage && <div className="alert alert-success" style={{ marginBottom: '12px' }}>✅ {successMessage}</div>}

            <div className="transcription-editor-card" style={{ marginBottom: '16px' }}>
                <p style={{ margin: '0 0 10px 0', fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                    Each ebook sentence, where it falls in the audio, and what the transcript heard there.
                    If the book lands in the wrong place, Re-align rebuilds this from the saved transcript,
                    without transcribing again.
                </p>
                <div style={{ display: 'flex', gap: '10px', alignItems: 'flex-end', flexWrap: 'wrap' }}>
                    <div style={{ flex: '1', minWidth: '180px' }}>
                        <label htmlFor="alignmentSearch" style={{ display: 'block', fontSize: '0.8rem', marginBottom: '4px' }}>Search</label>
                        <input
                            id="alignmentSearch"
                            type="search"
                            className="form-input"
                            aria-label="Search sentences"
                            value={query}
                            onChange={e => setQuery(e.target.value)}
                            placeholder="Find a word in the ebook or the transcript..."
                        />
                    </div>
                    {canEdit && (
                        <button className="btn btn-primary" onClick={handleRealign} disabled={realigning || points.length === 0}>
                            {realigning ? 'Re-aligning…' : 'Re-align'}
                        </button>
                    )}
                </div>
                {points.length > 0 && (
                    <p style={{ margin: '10px 0 0 0', fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                        <span>{matchedCount} matched, {points.length - matchedCount} filled in between matches</span>
                        {query.trim() && <span> · <span>{shown.length} of {points.length} sentences</span></span>}
                    </p>
                )}
            </div>

            <div
                className="transcription-editor-card"
                style={{ flex: '1', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '12px' }}
            >
                {points.length === 0 ? (
                    <div style={{ textAlign: 'center', color: 'var(--text-muted)', marginTop: '40px' }}>
                        No alignment for this book yet.
                    </div>
                ) : (
                    shown.map(pt => (
                        <div key={pt.id} style={{ display: 'flex', gap: '15px', paddingBottom: '12px', borderBottom: '1px solid var(--border)' }}>
                            <div style={{ width: '96px', flexShrink: 0, color: 'var(--text-muted)', fontSize: '0.8rem' }}>
                                <div>{formatTime(pt.start_ms)} – {formatTime(pt.end_ms)}</div>
                                <div style={{ marginTop: '4px', color: 'var(--accent-ink)' }}>Ch {pt.chapter}, S {pt.sentence}</div>
                                {!pt.matched && (
                                    <div style={{ marginTop: '4px' }} title="Not matched to the transcript: its time is estimated from the matched sentences around it">
                                        Filled in
                                    </div>
                                )}
                            </div>
                            <div style={{ flex: '1', minWidth: 0 }}>
                                <div style={{ fontSize: '0.9rem' }}>{pt.ebookText}</div>
                                <div style={{ marginTop: '4px', fontSize: '0.82rem', color: 'var(--text-secondary)', fontStyle: pt.heardText ? 'normal' : 'italic' }}>
                                    {pt.heardText || 'No transcript text in this range'}
                                </div>
                            </div>
                        </div>
                    ))
                )}
            </div>
        </div>
    )
}

export default TranscriptionEditorPage
