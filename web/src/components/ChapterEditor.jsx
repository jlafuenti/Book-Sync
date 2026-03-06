import React, { useState, useEffect } from 'react';
import { getAudiobookChapters, updateAudiobookChapters } from '../api';

function formatSecondsToHHMMSS(totalSeconds) {
    if (totalSeconds === undefined || totalSeconds === null) return "00:00:00";
    const h = Math.floor(totalSeconds / 3600);
    const m = Math.floor((totalSeconds % 3600) / 60);
    const s = Math.floor(totalSeconds % 60);
    return [
        h.toString().padStart(2, '0'),
        m.toString().padStart(2, '0'),
        s.toString().padStart(2, '0')
    ].join(':');
}

function parseHHMMSSToSeconds(timeString) {
    if (!timeString) return 0;
    const parts = timeString.split(':').map(Number);
    if (parts.length === 3) {
        return (parts[0] * 3600) + (parts[1] * 60) + parts[2];
    }
    if (parts.length === 2) {
        return (parts[0] * 60) + parts[1];
    }
    return parts[0] || 0;
}

export default function ChapterEditor({ bookId }) {
    const [chapters, setChapters] = useState([]);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');
    const [success, setSuccess] = useState('');

    useEffect(() => {
        loadChapters();
    }, [bookId]);

    const loadChapters = async () => {
        try {
            setLoading(true);
            setError('');
            const data = await getAudiobookChapters(bookId);
            setChapters(data);
        } catch (err) {
            setError(err.message || 'Failed to load chapters');
        } finally {
            setLoading(false);
        }
    };

    const handleSave = async () => {
        try {
            setSaving(true);
            setError('');
            setSuccess('');

            // Convert string inputs back to numbers and calculate correct end times
            const payload = chapters.map((ch, index) => {
                const start = typeof ch.start_time === 'string' ? parseHHMMSSToSeconds(ch.start_time) : ch.start_time;
                // We'll calculate end_time server side or just sequentially here
                return {
                    id: index,
                    title: ch.title,
                    start_time: start,
                    end_time: ch.end_time || 0
                };
            }).sort((a, b) => a.start_time - b.start_time);

            // Fix end times based on sorted array
            for (let i = 0; i < payload.length; i++) {
                if (i < payload.length - 1) {
                    payload[i].end_time = payload[i + 1].start_time;
                } else {
                    // Last chapter, just give it 10 hours if we don't know the file duration
                    payload[i].end_time = payload[i].start_time + 36000;
                }
            }

            await updateAudiobookChapters(bookId, payload);
            setSuccess('Chapters saved successfully!');
            await loadChapters();
        } catch (err) {
            setError(err.message || 'Failed to save chapters');
        } finally {
            setSaving(false);
        }
    };

    const addChapter = () => {
        const lastStart = chapters.length > 0 ? (typeof chapters[chapters.length - 1].start_time === 'string' ? parseHHMMSSToSeconds(chapters[chapters.length - 1].start_time) : chapters[chapters.length - 1].start_time) : 0;
        setChapters([...chapters, { id: chapters.length, title: `Chapter ${chapters.length + 1}`, start_time: formatSecondsToHHMMSS(lastStart + 300), end_time: 0 }]);
    };

    const removeChapter = (indexToRemove) => {
        setChapters(chapters.filter((_, idx) => idx !== indexToRemove));
    };

    const removeAll = () => {
        if (window.confirm('Are you sure you want to remove all chapters?')) {
            setChapters([]);
        }
    };

    const updateChapter = (index, field, value) => {
        const newChapters = [...chapters];
        newChapters[index][field] = value;
        setChapters(newChapters);
    };

    if (loading) return <div className="loading-spinner">Loading chapters...</div>;

    return (
        <div className="chapter-editor">
            <div className="chapter-toolbar">
                <button type="button" className="btn btn-secondary" onClick={addChapter}>+ Add Chapter</button>
                <button type="button" className="btn btn-danger" onClick={removeAll} disabled={chapters.length === 0}>🗑️ Remove All</button>
                <button type="button" className="btn btn-primary" onClick={handleSave} disabled={saving}>
                    {saving ? 'Saving...' : '💾 Save Chapters'}
                </button>
            </div>

            {error && <div className="alert alert-danger" style={{ marginTop: '1rem' }}>{error}</div>}
            {success && <div className="alert alert-success" style={{ marginTop: '1rem' }}>{success}</div>}

            {chapters.length === 0 ? (
                <p className="no-chapters-msg">No chapters found in this audiobook.</p>
            ) : (
                <table className="chapter-table">
                    <thead>
                        <tr>
                            <th>#</th>
                            <th>Start Time (HH:MM:SS)</th>
                            <th>Title</th>
                            <th>Actions</th>
                        </tr>
                    </thead>
                    <tbody>
                        {chapters.map((ch, idx) => (
                            <tr key={idx}>
                                <td>{idx + 1}</td>
                                <td>
                                    <input
                                        type="text"
                                        value={typeof ch.start_time === 'number' ? formatSecondsToHHMMSS(ch.start_time) : ch.start_time}
                                        onChange={(e) => updateChapter(idx, 'start_time', e.target.value)}
                                        placeholder="00:00:00"
                                        className="form-control"
                                    />
                                </td>
                                <td>
                                    <input
                                        type="text"
                                        value={ch.title}
                                        onChange={(e) => updateChapter(idx, 'title', e.target.value)}
                                        className="form-control"
                                    />
                                </td>
                                <td>
                                    <button type="button" className="btn btn-danger btn-sm" onClick={() => removeChapter(idx)}>X</button>
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            )}

            <style>{`
                .chapter-editor {
                    margin-top: 1rem;
                }
                .chapter-toolbar {
                    display: flex;
                    gap: 1rem;
                    margin-bottom: 1rem;
                }
                .chapter-table {
                    width: 100%;
                    border-collapse: collapse;
                    margin-top: 1rem;
                }
                .chapter-table th, .chapter-table td {
                    padding: 0.75rem;
                    border-bottom: 1px solid var(--border-color);
                    text-align: left;
                }
                .chapter-table th {
                    background-color: rgba(255,255,255,0.05);
                }
                .chapter-table td {
                    vertical-align: middle;
                }
                .chapter-table input {
                    width: 100%;
                    background: var(--bg-color);
                    border: 1px solid var(--border-color);
                    color: white;
                    padding: 0.5rem;
                    border-radius: 4px;
                }
                .btn-danger {
                    background-color: #e74c3c;
                    border: none;
                    color: white;
                }
                .btn-danger:hover:not(:disabled) {
                    background-color: #c0392b;
                }
                .btn-sm {
                    padding: 0.25rem 0.5rem;
                    font-size: 0.875rem;
                }
                .no-chapters-msg {
                    padding: 2rem;
                    text-align: center;
                    color: #888;
                    background: rgba(255,255,255,0.02);
                    border-radius: 8px;
                }
            `}</style>
        </div>
    );
}
