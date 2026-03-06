import React, { useState, useEffect } from 'react';
import { getMetadataDiscrepancies, resolveMetadataDiscrepancies } from '../api';

export default function MetadataCleanupModal({ onClose, onComplete }) {
    const [discrepancies, setDiscrepancies] = useState([]);
    const [currentIndex, setCurrentIndex] = useState(0);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [saving, setSaving] = useState(false);

    // selected[field] = 'ebook' | 'audiobook' | null
    const [selected, setSelected] = useState({});

    useEffect(() => {
        loadData();
    }, []);

    const loadData = async () => {
        setLoading(true);
        setError('');
        try {
            const data = await getMetadataDiscrepancies();
            setDiscrepancies(data);
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        setSelected({});
    }, [currentIndex]);

    const handleSelect = (field, source) => {
        setSelected(prev => ({
            ...prev,
            [field]: prev[field] === source ? null : source
        }));
    };

    const handleSaveAndNext = async () => {
        const currentPair = discrepancies[currentIndex];
        if (!currentPair) return;

        setSaving(true);
        setError('');
        try {
            const ebook_updates = {};
            const audiobook_updates = {};

            currentPair.discrepancies.forEach(d => {
                const choice = selected[d.field];
                if (choice === 'ebook') {
                    audiobook_updates[d.field] = d.ebook_value;
                } else if (choice === 'audiobook') {
                    ebook_updates[d.field] = d.audiobook_value;
                }
            });

            await resolveMetadataDiscrepancies(currentPair.pair_id, {
                ebook_updates,
                audiobook_updates
            });

            handleNext();
        } catch (err) {
            setError(err.message);
        } finally {
            setSaving(false);
        }
    };

    const handleNext = () => {
        if (currentIndex < discrepancies.length - 1) {
            setCurrentIndex(prev => prev + 1);
        } else {
            onComplete(); // Done resolving all
        }
    };

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-container" style={{ maxWidth: '800px' }} onClick={e => e.stopPropagation()}>
                <div className="modal-header">
                    <h2 className="modal-title">✨ Clean Up Metadata</h2>
                    <button className="btn btn-secondary btn-sm" onClick={onClose}>✕</button>
                </div>
                <div className="modal-content">
                    {loading ? (
                        <div className="loading-page"><div className="spinner"></div> Finding discrepancies...</div>
                    ) : error ? (
                        <div className="alert alert-error">⚠️ {error}</div>
                    ) : discrepancies.length === 0 ? (
                        <div className="empty-state">
                            <h3>All Clean! 🧹</h3>
                            <p>No metadata discrepancies found between any of your paired books.</p>
                            <button className="btn btn-primary" onClick={onClose} style={{ marginTop: '1rem' }}>Done</button>
                        </div>
                    ) : (
                        <div>
                            <div style={{ marginBottom: '1rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                <h3>Resolving Pair {currentIndex + 1} of {discrepancies.length}</h3>
                                <div style={{ fontSize: '1.2rem', fontWeight: 'bold' }}>
                                    {discrepancies[currentIndex].title}
                                </div>
                            </div>

                            <p style={{ marginBottom: '1rem', color: 'var(--text-muted)' }}>
                                Click a value to select it as the correct one. The unselected format will be updated to match.
                            </p>

                            <table className="table" style={{ width: '100%', marginBottom: '2rem' }}>
                                <thead>
                                    <tr>
                                        <th>Field</th>
                                        <th>EBook Value 📚</th>
                                        <th>Audiobook Value 🎧</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {discrepancies[currentIndex].discrepancies.map(d => (
                                        <tr key={d.field}>
                                            <td style={{ fontWeight: 500, textTransform: 'capitalize' }}>
                                                {d.field.replace('_', ' ')}
                                            </td>
                                            <td
                                                style={{
                                                    cursor: 'pointer',
                                                    backgroundColor: selected[d.field] === 'ebook' ? 'var(--bg-card-hover)' : 'transparent',
                                                    border: selected[d.field] === 'ebook' ? '2px solid var(--primary)' : '2px solid transparent',
                                                    borderRadius: '4px',
                                                    padding: '8px'
                                                }}
                                                onClick={() => handleSelect(d.field, 'ebook')}
                                            >
                                                {d.field === 'cover_path' ? (
                                                    d.ebook_value ? <img src={d.ebook_value} alt="Ebook Cover" style={{ height: '80px', borderRadius: '4px' }} /> : <em>No Cover</em>
                                                ) : (
                                                    d.ebook_value || <em>Empty</em>
                                                )}
                                            </td>
                                            <td
                                                style={{
                                                    cursor: 'pointer',
                                                    backgroundColor: selected[d.field] === 'audiobook' ? 'var(--bg-card-hover)' : 'transparent',
                                                    border: selected[d.field] === 'audiobook' ? '2px solid var(--primary)' : '2px solid transparent',
                                                    borderRadius: '4px',
                                                    padding: '8px'
                                                }}
                                                onClick={() => handleSelect(d.field, 'audiobook')}
                                            >
                                                {d.field === 'cover_path' ? (
                                                    d.audiobook_value ? <img src={d.audiobook_value} alt="Audiobook Cover" style={{ height: '80px', borderRadius: '4px' }} /> : <em>No Cover</em>
                                                ) : (
                                                    d.audiobook_value || <em>Empty</em>
                                                )}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>

                        </div>
                    )}
                </div>
                {discrepancies.length > 0 && !loading && !error && (
                    <div className="modal-footer" style={{ justifyContent: 'space-between' }}>
                        <button className="btn btn-secondary" onClick={handleNext} disabled={saving}>
                            Skip
                        </button>
                        <button
                            className="btn btn-primary"
                            onClick={handleSaveAndNext}
                            disabled={saving}
                        >
                            {saving ? 'Saving...' : 'Save & Next'}
                        </button>
                    </div>
                )}
            </div>
        </div>
    );
}
