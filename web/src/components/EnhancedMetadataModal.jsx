import React, { useState } from 'react';
import { uploadEbookCover, uploadAudiobookCover } from '../api';

function formatBytes(bytes) {
    if (!bytes) return '—'
    if (bytes < 1024) return bytes + ' B'
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
    if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
    return (bytes / (1024 * 1024 * 1024)).toFixed(2) + ' GB'
}

function formatDuration(seconds) {
    if (!seconds) return '—'
    const h = Math.floor(seconds / 3600)
    const m = Math.floor((seconds % 3600) / 60)
    const s = seconds % 60
    if (h > 0) return `${h}h ${m}m ${s}s`
    if (m > 0) return `${m}m ${s}s`
    return `${s}s`
}

function formatDate(iso) {
    if (!iso) return '—'
    return new Date(iso).toLocaleDateString('en-US', {
        year: 'numeric', month: 'short', day: 'numeric',
        hour: '2-digit', minute: '2-digit'
    })
}

export default function EnhancedMetadataModal({ book, type, onClose, onSave }) {
    const [activeTab, setActiveTab] = useState('Details');
    const [saving, setSaving] = useState(false);

    // Details Tab Form State
    const [formData, setFormData] = useState({
        title: book.title || '',
        author: book.author || '',
        series: book.series || '',
        series_index: book.series_index || '',
        description: book.description || '',
        publisher: book.publisher || '',
        publish_year: book.publish_year || '',
        language: book.language || '',
        genres: book.genres || '',
        tags: book.tags || '',
        narrators: book.narrators || '',
        isbn: book.isbn || '',
        asin: book.asin || '',
        is_explicit: book.is_explicit || false,
        is_abridged: book.is_abridged || false,
    });

    // Cover Tab State
    const [coverFile, setCoverFile] = useState(null);
    const [uploadingCover, setUploadingCover] = useState(false);
    const [coverPreview, setCoverPreview] = useState(book.cover_path || null);

    const handleChange = (e) => {
        const { name, value, type: inputType, checked } = e.target;
        setFormData(prev => ({
            ...prev,
            [name]: inputType === 'checkbox' ? checked : value
        }));
    };

    const handleSubmit = async (e) => {
        e.preventDefault();
        setSaving(true);
        try {
            await onSave(book.id, {
                title: formData.title,
                author: formData.author,
                series: formData.series || null,
                series_index: formData.series_index ? parseFloat(formData.series_index) : null,
                description: formData.description || null,
                publisher: formData.publisher || null,
                publish_year: formData.publish_year ? parseInt(formData.publish_year, 10) : null,
                language: formData.language || null,
                genres: formData.genres || null,
                tags: formData.tags || null,
                narrators: formData.narrators || null,
                isbn: formData.isbn || null,
                asin: formData.asin || null,
                is_explicit: formData.is_explicit,
                is_abridged: formData.is_abridged,
            });
            onClose();
        } finally {
            setSaving(false);
        }
    };

    const handleCoverChange = async (e) => {
        if (e.target.files && e.target.files[0]) {
            const file = e.target.files[0];
            setCoverFile(file);
            setCoverPreview(URL.createObjectURL(file));
        }
    };

    const handleUploadCover = async () => {
        if (!coverFile) return;
        setUploadingCover(true);
        try {
            if (type === 'ebook') {
                await uploadEbookCover(book.id, coverFile);
            } else {
                await uploadAudiobookCover(book.id, coverFile);
            }
            alert('Cover uploaded successfully! Changes will appear when you reload.');
        } catch (err) {
            alert('Failed to upload cover: ' + err.message);
        } finally {
            setUploadingCover(false);
        }
    };

    const isAudiobook = type === 'audiobook';

    return (
        <div className="modal-overlay">
            <div className="modal modal-xl">
                <div className="modal-header">
                    <h3>Edit {isAudiobook ? 'Audiobook' : 'Ebook'}</h3>
                    <button className="btn-close" onClick={onClose} type="button">&times;</button>
                </div>

                <div className="modal-tabs">
                    {['Details', 'Cover', 'Files', 'Match', 'Chapters'].map(tab => {
                        if (tab === 'Chapters' && !isAudiobook) return null;
                        return (
                            <button
                                key={tab}
                                className={`modal-tab ${activeTab === tab ? 'active' : ''}`}
                                onClick={() => setActiveTab(tab)}
                                type="button"
                            >
                                {tab}
                            </button>
                        )
                    })}
                </div>

                <div className="modal-body p-0">
                    {activeTab === 'Details' && (
                        <form onSubmit={handleSubmit} className="p-4" id="metadata-form">
                            <div className="form-grid">
                                <div className="form-group span-2">
                                    <label>Title</label>
                                    <input required className="form-input" name="title" value={formData.title} onChange={handleChange} />
                                </div>
                                <div className="form-group span-2">
                                    <label>Author</label>
                                    <input className="form-input" name="author" value={formData.author} onChange={handleChange} />
                                </div>

                                <div className="form-group span-2">
                                    <label>Description</label>
                                    <textarea className="form-input" name="description" value={formData.description} onChange={handleChange} rows="4"></textarea>
                                </div>

                                <div className="form-group">
                                    <label>Series</label>
                                    <input className="form-input" name="series" value={formData.series} onChange={handleChange} />
                                </div>
                                <div className="form-group">
                                    <label>Series Index</label>
                                    <input className="form-input" type="number" step="0.1" name="series_index" value={formData.series_index} onChange={handleChange} />
                                </div>

                                <div className="form-group">
                                    <label>Publisher</label>
                                    <input className="form-input" name="publisher" value={formData.publisher} onChange={handleChange} />
                                </div>
                                <div className="form-group">
                                    <label>Publish Year</label>
                                    <input className="form-input" type="number" name="publish_year" value={formData.publish_year} onChange={handleChange} />
                                </div>

                                <div className="form-group">
                                    <label>Genres</label>
                                    <input className="form-input" name="genres" value={formData.genres} onChange={handleChange} placeholder="Comma separated" />
                                </div>
                                <div className="form-group">
                                    <label>Tags</label>
                                    <input className="form-input" name="tags" value={formData.tags} onChange={handleChange} placeholder="Comma separated" />
                                </div>

                                <div className="form-group">
                                    <label>ISBN</label>
                                    <input className="form-input" name="isbn" value={formData.isbn} onChange={handleChange} />
                                </div>
                                <div className="form-group">
                                    <label>ASIN</label>
                                    <input className="form-input" name="asin" value={formData.asin} onChange={handleChange} />
                                </div>

                                <div className="form-group">
                                    <label>Language</label>
                                    <input className="form-input" name="language" value={formData.language} onChange={handleChange} />
                                </div>
                                {isAudiobook && (
                                    <div className="form-group">
                                        <label>Narrators</label>
                                        <input className="form-input" name="narrators" value={formData.narrators} onChange={handleChange} />
                                    </div>
                                )}

                                <div className="form-group checkbox-group span-2 mt-2" style={{ display: 'flex' }}>
                                    <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer' }}>
                                        <input type="checkbox" name="is_explicit" checked={formData.is_explicit} onChange={handleChange} />
                                        Explicit Content
                                    </label>
                                    <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer', marginLeft: '24px' }}>
                                        <input type="checkbox" name="is_abridged" checked={formData.is_abridged} onChange={handleChange} />
                                        Abridged
                                    </label>
                                </div>

                            </div>
                        </form>
                    )}

                    {activeTab === 'Cover' && (
                        <div className="p-4" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '20px' }}>
                            <div className="cover-preview" style={{ width: '200px', height: '300px', backgroundColor: 'var(--bg-input)', display: 'flex', alignItems: 'center', justifyContent: 'center', borderRadius: '8px', overflow: 'hidden' }}>
                                {coverPreview ? (
                                    <img src={coverPreview} alt="Cover Preview" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                                ) : (
                                    <span style={{ fontSize: '3rem', color: 'var(--text-muted)' }}>{isAudiobook ? '🎧' : '📚'}</span>
                                )}
                            </div>

                            <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                                <label className="btn btn-secondary" style={{ cursor: 'pointer' }}>
                                    Choose File
                                    <input type="file" accept="image/*" style={{ display: 'none' }} onChange={handleCoverChange} />
                                </label>
                                <button
                                    className="btn btn-primary"
                                    onClick={handleUploadCover}
                                    disabled={!coverFile || uploadingCover}
                                >
                                    {uploadingCover ? 'Uploading...' : 'Upload Cover'}
                                </button>
                            </div>

                            {book.cover_path && (
                                <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                                    Current cover: {book.cover_path}
                                </p>
                            )}
                        </div>
                    )}

                    {activeTab === 'Files' && (
                        <div className="p-4">
                            <table className="file-info-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
                                <tbody>
                                    <tr style={{ borderBottom: '1px solid var(--border)' }}><td style={{ padding: '8px' }}>File Path</td><td style={{ padding: '8px', wordBreak: 'break-all' }}><code>{book.file_path}</code></td></tr>
                                    <tr style={{ borderBottom: '1px solid var(--border)' }}><td style={{ padding: '8px' }}>Filename</td><td style={{ padding: '8px' }}><code>{book.filename}</code></td></tr>
                                    <tr style={{ borderBottom: '1px solid var(--border)' }}><td style={{ padding: '8px' }}>Format</td><td style={{ padding: '8px' }}>{book.format}</td></tr>
                                    <tr style={{ borderBottom: '1px solid var(--border)' }}><td style={{ padding: '8px' }}>File Size</td><td style={{ padding: '8px' }}>{formatBytes(book.file_size)}</td></tr>
                                    {isAudiobook && <tr style={{ borderBottom: '1px solid var(--border)' }}><td style={{ padding: '8px' }}>Duration</td><td style={{ padding: '8px' }}>{formatDuration(book.duration_seconds)}</td></tr>}
                                    <tr style={{ borderBottom: '1px solid var(--border)' }}><td style={{ padding: '8px' }}>File Hash</td><td style={{ padding: '8px', wordBreak: 'break-all' }}><code>{book.file_hash || '—'}</code></td></tr>
                                    <tr style={{ borderBottom: '1px solid var(--border)' }}><td style={{ padding: '8px' }}>Metadata Source</td><td style={{ padding: '8px' }}>{book.metadata_source || '—'}</td></tr>
                                    <tr style={{ borderBottom: '1px solid var(--border)' }}><td style={{ padding: '8px' }}>Metadata Pattern</td><td style={{ padding: '8px' }}><code>{book.metadata_pattern || '—'}</code></td></tr>
                                    <tr style={{ borderBottom: '1px solid var(--border)' }}><td style={{ padding: '8px' }}>Added</td><td style={{ padding: '8px' }}>{formatDate(book.uploaded_at)}</td></tr>
                                </tbody>
                            </table>
                        </div>
                    )}

                    {activeTab === 'Match' && (
                        <div className="p-4 flex-center empty-state">
                            <div className="icon">🔍</div>
                            <h3>Quick Match</h3>
                            <p>External Match functionality is coming in W3 Phase.</p>
                        </div>
                    )}

                    {activeTab === 'Chapters' && (
                        <div className="p-4 flex-center empty-state">
                            <div className="icon">📑</div>
                            <h3>Chapter Editor</h3>
                            <p>Chapter Editor functionality is coming in W5 Phase.</p>
                        </div>
                    )}
                </div>

                <div className="modal-actions" style={{ padding: '16px', borderTop: '1px solid var(--border)', display: 'flex', justifyContent: 'flex-end', gap: '10px' }}>
                    <button type="button" className="btn btn-secondary" onClick={onClose} disabled={saving}>
                        Close
                    </button>
                    {activeTab === 'Details' && (
                        <button type="submit" form="metadata-form" className="btn btn-primary" disabled={saving}>
                            {saving ? 'Saving...' : 'Save Details'}
                        </button>
                    )}
                </div>
            </div>
        </div>
    );
}
