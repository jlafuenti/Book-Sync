import React, { useState } from 'react';
import { searchMetadata } from '../api';

export default function MatchTab({ currentData, onApply }) {
    const [provider, setProvider] = useState('google');
    const [query, setQuery] = useState(currentData.title || currentData.isbn || '');
    const [author, setAuthor] = useState(currentData.author || '');
    const [loading, setLoading] = useState(false);
    const [results, setResults] = useState([]);
    const [error, setError] = useState('');

    // View state: 'search' | 'compare'
    const [view, setView] = useState('search');
    const [selectedResult, setSelectedResult] = useState(null);

    // Checkbox state for comparison view
    const [selectedFields, setSelectedFields] = useState({
        title: false,
        author: false,
        publisher: false,
        publish_year: false,
        description: false,
        isbn: false,
        cover_url: false
    });

    const handleSearch = async (e) => {
        e.preventDefault();
        setLoading(true);
        setError('');
        try {
            const data = await searchMetadata(provider, query, author);
            setResults(data);
        } catch (err) {
            setError(err.message || 'Failed to search metadata');
        } finally {
            setLoading(false);
        }
    };

    const handleSelectResult = (result) => {
        setSelectedResult(result);

        // Smart defaults: Check fields that are missing locally but present remotely
        setSelectedFields({
            title: !currentData.title && !!result.title,
            author: !currentData.author && !!result.author,
            publisher: !currentData.publisher && !!result.publisher,
            publish_year: !currentData.publish_year && !!result.publish_year,
            description: !currentData.description && !!result.description,
            isbn: !currentData.isbn && !!result.isbn,
            cover_url: !currentData.cover_path && !!result.cover_url
        });

        setView('compare');
    };

    const handleFieldToggle = (field) => {
        setSelectedFields(prev => ({ ...prev, [field]: !prev[field] }));
    };

    const handleApplySelected = () => {
        const payload = {};
        if (selectedFields.title) payload.title = selectedResult.title;
        if (selectedFields.author) payload.author = selectedResult.author;
        if (selectedFields.publisher) payload.publisher = selectedResult.publisher;
        if (selectedFields.publish_year) payload.publish_year = selectedResult.publish_year;
        if (selectedFields.description) payload.description = selectedResult.description;
        if (selectedFields.isbn) payload.isbn = selectedResult.isbn;
        if (selectedFields.cover_url) payload.coverUrl = selectedResult.cover_url;

        onApply(payload);
    };

    if (view === 'search') {
        return (
            <div className="match-tab p-4">
                <form onSubmit={handleSearch} className="match-search-form">
                    <div className="form-group row">
                        <div className="col-md-3">
                            <label>Provider</label>
                            <select
                                className="form-input"
                                value={provider}
                                onChange={e => setProvider(e.target.value)}
                            >
                                <option value="google">Google Books</option>
                                <option value="openlibrary">Open Library</option>
                            </select>
                        </div>
                        <div className="col-md-5">
                            <label>Title or ISBN</label>
                            <input
                                className="form-input"
                                value={query}
                                onChange={e => setQuery(e.target.value)}
                                required
                            />
                        </div>
                        <div className="col-md-4">
                            <label>Author (Optional)</label>
                            <input
                                className="form-input"
                                value={author}
                                onChange={e => setAuthor(e.target.value)}
                            />
                        </div>
                    </div>
                    <button type="submit" className="btn btn-primary mt-3" disabled={loading}>
                        {loading ? 'Searching...' : 'Search'}
                    </button>
                </form>

                {error && <div className="alert alert-danger mt-3">{error}</div>}

                <div className="match-results mt-4">
                    {results.length === 0 && !loading && !error && (
                        <p className="text-muted">Enter a search above to find metadata.</p>
                    )}

                    <div className="match-grid">
                        {results.map(res => (
                            <div key={res.id} className="match-card">
                                <div className="match-card-cover">
                                    {res.cover_url ? (
                                        <img src={res.cover_url} alt="Cover" />
                                    ) : (
                                        <div className="no-cover">No Cover</div>
                                    )}
                                </div>
                                <div className="match-card-info">
                                    <h4>{res.title}</h4>
                                    <p className="text-muted">{res.author || 'Unknown Author'}</p>
                                    <p className="text-small">
                                        {res.publish_year || 'Unknown Year'}
                                        {res.publisher ? ` · ${res.publisher}` : ''}
                                    </p>
                                    <button
                                        type="button"
                                        className="btn btn-secondary btn-sm mt-auto"
                                        onClick={() => handleSelectResult(res)}
                                    >
                                        Select
                                    </button>
                                </div>
                            </div>
                        ))}
                    </div>
                </div>

                <style>{`
                    .match-grid {
                        display: grid;
                        grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
                        gap: 1rem;
                    }
                    .match-card {
                        background: var(--bg-hover);
                        border: 1px solid var(--border-color);
                        border-radius: 8px;
                        padding: 1rem;
                        display: flex;
                        gap: 1rem;
                    }
                    .match-card-cover {
                        width: 80px;
                        height: 120px;
                        flex-shrink: 0;
                        background: rgba(255,255,255,0.05);
                        border-radius: 4px;
                        overflow: hidden;
                    }
                    .match-card-cover img {
                        width: 100%;
                        height: 100%;
                        object-fit: cover;
                    }
                    .no-cover {
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        height: 100%;
                        font-size: 0.8rem;
                        color: #666;
                        text-align: center;
                    }
                    .match-card-info {
                        display: flex;
                        flex-direction: column;
                        flex-grow: 1;
                    }
                    .match-card-info h4 { margin: 0 0 0.25rem 0; font-size: 1rem; }
                    .match-card-info p { margin: 0 0 0.25rem 0; }
                    .text-small { font-size: 0.85rem; color: #aaa; }
                `}</style>
            </div>
        );
    }

    if (view === 'compare') {
        return (
            <div className="match-compare p-4">
                <div className="compare-header flex-center" style={{ justifyContent: 'space-between', marginBottom: '1rem' }}>
                    <h3>Review Missing Information</h3>
                    <div style={{ display: 'flex', gap: '10px' }}>
                        <button className="btn btn-secondary" onClick={() => setView('search')}>
                            Cancel
                        </button>
                        <button className="btn btn-primary" onClick={handleApplySelected}>
                            Apply Selected
                        </button>
                    </div>
                </div>

                <p className="text-muted" style={{ marginBottom: '1.5rem' }}>
                    Select which fields you want to pull from the remote search. Empty local fields have been checked automatically.
                </p>

                <table className="compare-table">
                    <thead>
                        <tr>
                            <th style={{ width: '50px', textAlign: 'center' }}>Use</th>
                            <th>Field</th>
                            <th style={{ width: '40%' }}>Current (Local)</th>
                            <th style={{ width: '40%' }}>New (Remote)</th>
                        </tr>
                    </thead>
                    <tbody>
                        {/* Cover row */}
                        <tr>
                            <td style={{ textAlign: 'center' }}>
                                <input
                                    type="checkbox"
                                    checked={selectedFields.cover_url}
                                    onChange={() => handleFieldToggle('cover_url')}
                                    disabled={!selectedResult.cover_url}
                                />
                            </td>
                            <td><strong>Cover</strong></td>
                            <td>
                                {currentData.cover_path ? (
                                    <span className="badge badge-success">Has Cover</span>
                                ) : (
                                    <span className="text-muted">None</span>
                                )}
                            </td>
                            <td>
                                {selectedResult.cover_url ? (
                                    <img src={selectedResult.cover_url} alt="Remote Cover" style={{ height: '60px', borderRadius: '4px' }} />
                                ) : (
                                    <span className="text-muted">None</span>
                                )}
                            </td>
                        </tr>

                        {/* Text fields */}
                        {['title', 'author', 'publisher', 'publish_year', 'isbn'].map(field => (
                            <tr key={field}>
                                <td style={{ textAlign: 'center' }}>
                                    <input
                                        type="checkbox"
                                        checked={selectedFields[field]}
                                        onChange={() => handleFieldToggle(field)}
                                        disabled={!selectedResult[field]}
                                    />
                                </td>
                                <td style={{ textTransform: 'capitalize' }}><strong>{field.replace('_', ' ')}</strong></td>
                                <td>{currentData[field] || <span className="text-muted">Empty</span>}</td>
                                <td>{selectedResult[field] || <span className="text-muted">Empty</span>}</td>
                            </tr>
                        ))}

                        {/* Description (Truncated) */}
                        <tr>
                            <td style={{ textAlign: 'center' }}>
                                <input
                                    type="checkbox"
                                    checked={selectedFields.description}
                                    onChange={() => handleFieldToggle('description')}
                                    disabled={!selectedResult.description}
                                />
                            </td>
                            <td><strong>Description</strong></td>
                            <td>
                                {currentData.description ? '...' + currentData.description.substring(0, 50) + '...' : <span className="text-muted">Empty</span>}
                            </td>
                            <td>
                                {selectedResult.description ? '...' + selectedResult.description.substring(0, 100) + '...' : <span className="text-muted">Empty</span>}
                            </td>
                        </tr>
                    </tbody>
                </table>

                <style>{`
                    .compare-table {
                        width: 100%;
                        border-collapse: collapse;
                        background: rgba(0,0,0,0.2);
                        border-radius: 8px;
                        overflow: hidden;
                    }
                    .compare-table th, .compare-table td {
                        padding: 12px;
                        text-align: left;
                        border-bottom: 1px solid var(--border-color);
                    }
                    .compare-table th {
                        background: rgba(255,255,255,0.05);
                    }
                    .compare-table tr:hover td {
                        background: rgba(255,255,255,0.02);
                    }
                    .badge-success {
                        background: #2ecc71;
                        color: #000;
                        padding: 2px 6px;
                        border-radius: 4px;
                        font-size: 0.8rem;
                    }
                `}</style>
            </div>
        );
    }

    return null;
}
