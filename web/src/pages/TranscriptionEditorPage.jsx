import React, { useState, useEffect, useMemo, useRef } from 'react'
import { useParams, Link } from 'react-router-dom'
import { getSyncMap, updateTranscriptionText, getPair } from '../api'
import './TranscriptionPage.css'

function TranscriptionEditorPage() {
    const { pairId } = useParams()
    const [points, setPoints] = useState([])
    const [pair, setPair] = useState(null)
    const [loading, setLoading] = useState(true)
    const [saving, setSaving] = useState(false)
    const [error, setError] = useState('')
    const [successMessage, setSuccessMessage] = useState('')

    // Find and Replace state
    const [findText, setFindText] = useState('')
    const [replaceText, setReplaceText] = useState('')

    // Track which points have been edited
    const [editedPoints, setEditedPoints] = useState({})

    const listRef = useRef(null)

    useEffect(() => {
        const load = async () => {
            try {
                // Fetch the sync map points
                const data = await getSyncMap(pairId)
                // Map the data into something easier to edit
                const pts = (data.sync_points || []).map(pt => ({
                    id: pt.id,
                    originalText: pt.audio_text !== null ? pt.audio_text : pt.epub_text_preview || '',
                    text: pt.audio_text !== null ? pt.audio_text : pt.epub_text_preview || '',
                    start_ms: pt.audio_start_ms,
                    end_ms: pt.audio_end_ms,
                    chapter: pt.epub_chapter,
                    sentence: pt.epub_sentence_index
                }))
                setPoints(pts)
            } catch (err) {
                setError(err.message)
            } finally {
                setLoading(false)
            }

            // The pair is only the subheading's title/author (issue #277). It
            // used to come from `getPairs()` — the whole library, filtered
            // client-side to one row. Fetch the one, and keep its failure off
            // the editor: a missing pair must not blank the points.
            try {
                setPair(await getPair(pairId))
            } catch {
                // Subheading stays empty; the sync map is what matters here.
            }
        }
        load()
    }, [pairId])

    const handleTextChange = (id, newText) => {
        setPoints(points.map(pt => pt.id === id ? { ...pt, text: newText } : pt))

        // Mark as edited
        setEditedPoints(prev => ({
            ...prev,
            [id]: true
        }))
    }

    const handleFindReplaceAll = () => {
        if (!findText) return
        let count = 0
        const newPoints = points.map(pt => {
            if (pt.text.includes(findText)) {
                count++
                const updatedText = pt.text.split(findText).join(replaceText)
                setEditedPoints(prev => ({ ...prev, [pt.id]: true }))
                return { ...pt, text: updatedText }
            }
            return pt
        })

        setPoints(newPoints)
        if (count > 0) {
            setSuccessMessage(`Replaced ${count} instances.`)
            setTimeout(() => setSuccessMessage(''), 3000)
        } else {
            setError(`"${findText}" not found.`)
            setTimeout(() => setError(''), 3000)
        }
    }

    const handleSave = async () => {
        setSaving(true)
        setError('')
        setSuccessMessage('')
        try {
            // Find all points that have been edited
            const toUpdate = points
                .filter(pt => editedPoints[pt.id] && pt.text !== pt.originalText)
                .map(pt => ({
                    id: pt.id,
                    audio_text: pt.text
                }))

            if (toUpdate.length === 0) {
                setSuccessMessage('No changes to save.')
                setSaving(false)
                return
            }

            const res = await updateTranscriptionText(pairId, toUpdate)
            setSuccessMessage(`Successfully updated ${res.updated} points.`)

            // Re-sync originalText
            setPoints(points.map(pt => ({ ...pt, originalText: pt.text })))
            setEditedPoints({})

            setTimeout(() => setSuccessMessage(''), 3000)
        } catch (err) {
            setError(err.message)
        } finally {
            setSaving(false)
        }
    }

    const formatTime = (ms) => {
        const totalSec = Math.floor(ms / 1000)
        const m = Math.floor(totalSec / 60)
        const s = totalSec % 60
        return `${m}:${s.toString().padStart(2, '0')}`
    }

    if (loading) {
        return <div className="loading-page"><div className="spinner"></div> Loading transcription...</div>
    }

    return (
        <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '4px' }}>
                <Link to="/transcription/transcribed" className="btn btn-secondary btn-sm" style={{ textDecoration: 'none' }}>
                    ← Back
                </Link>
                <h2 style={{ margin: 0, fontSize: '1.2rem', fontWeight: 700 }}>Edit Transcription</h2>
            </div>
            {pair && (
                <p style={{ margin: '0 0 16px 0', color: 'var(--text-secondary)', fontSize: '0.9rem' }}>
                    {pair.ebook.title}{pair.ebook.author && ` by ${pair.ebook.author}`}
                </p>
            )}

            {error && <div className="alert alert-error" style={{ marginBottom: '12px' }}>⚠️ {error}</div>}
            {successMessage && <div className="alert alert-success" style={{ marginBottom: '12px' }}>✅ {successMessage}</div>}

            <div className="transcription-editor-card" style={{ marginBottom: '16px' }}>
                <h3 style={{ margin: '0 0 10px 0', fontSize: '1rem' }}>Find & Replace</h3>
                <div style={{ display: 'flex', gap: '10px', alignItems: 'flex-end', flexWrap: 'wrap' }}>
                    <div style={{ flex: '1', minWidth: '150px' }}>
                        <label style={{ display: 'block', fontSize: '0.8rem', marginBottom: '4px' }}>Find</label>
                        <input
                            type="text"
                            className="form-input"
                            value={findText}
                            onChange={e => setFindText(e.target.value)}
                            placeholder="Word to find..."
                        />
                    </div>
                    <div style={{ flex: '1', minWidth: '150px' }}>
                        <label style={{ display: 'block', fontSize: '0.8rem', marginBottom: '4px' }}>Replace With</label>
                        <input
                            type="text"
                            className="form-input"
                            value={replaceText}
                            onChange={e => setReplaceText(e.target.value)}
                            placeholder="Replacement word..."
                        />
                    </div>
                    <button className="btn btn-secondary" onClick={handleFindReplaceAll} disabled={!findText}>
                        Replace All
                    </button>
                    <button className="btn btn-primary" onClick={handleSave} disabled={saving || Object.keys(editedPoints).length === 0}>
                        {saving ? 'Saving...' : 'Save Changes'}
                    </button>
                </div>
            </div>

            <div
                ref={listRef}
                className="transcription-editor-card"
                style={{
                    flex: '1',
                    overflowY: 'auto',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '15px'
                }}
            >
                {points.length === 0 ? (
                    <div style={{ textAlign: 'center', color: 'var(--text-muted)', marginTop: '40px' }}>
                        No transcription points found for this sync map.
                    </div>
                ) : (
                    points.map(pt => (
                        <div key={pt.id} style={{ display: 'flex', gap: '15px', paddingBottom: '15px', borderBottom: '1px solid var(--border)' }}>
                            <div style={{ width: '80px', flexShrink: 0, color: 'var(--text-muted)', fontSize: '0.85rem' }}>
                                <div>{formatTime(pt.start_ms)}</div>
                                <div>↓</div>
                                <div>{formatTime(pt.end_ms)}</div>
                                <div style={{ marginTop: '5px', fontSize: '0.75rem', color: 'var(--accent)' }}>
                                    Ch {pt.chapter}, S {pt.sentence}
                                </div>
                            </div>
                            <div style={{ flex: '1' }}>
                                <textarea
                                    className="form-input"
                                    style={{
                                        width: '100%',
                                        minHeight: '60px',
                                        resize: 'vertical',
                                        backgroundColor: pt.text !== pt.originalText ? 'var(--accent-light)' : 'var(--bg-card)',
                                        borderColor: pt.text !== pt.originalText ? 'var(--accent)' : 'var(--border)'
                                    }}
                                    value={pt.text}
                                    onChange={(e) => handleTextChange(pt.id, e.target.value)}
                                />
                            </div>
                        </div>
                    ))
                )}
            </div>
        </div>
    )
}

export default TranscriptionEditorPage
