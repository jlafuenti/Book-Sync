import React, { useState, useEffect, useRef } from 'react';
import { searchMetadata, getSettings } from '../api';

// Text/number fields shared between MatchResult and the local book form.
// cover_url and description get dedicated rows in the compare table.
const MATCH_FIELDS = [
    'title', 'author', 'series', 'series_index', 'publisher', 'publish_year',
    'genres', 'tags', 'language', 'narrators', 'isbn', 'asin',
];

const emptyFieldSelection = () => Object.fromEntries(
    [...MATCH_FIELDS, 'description', 'cover_url'].map(f => [f, false])
);

const hasValue = (v) => v !== null && v !== undefined && v !== '';

export default function MatchTab({ currentData, onApply, bookType }) {
    const [provider, setProvider] = useState(bookType === 'audiobook' ? 'audible' : 'google');
    const providerTouched = useRef(false);

    // Ebooks prefer Hardcover, but only when a token is configured. Read the
    // derived `hardcover_configured` boolean rather than the token: since issue
    // #263 the settings GET hands anyone below admin an allow-list of
    // { abs_enabled, hardcover_configured } and no token field at all, masked or
    // otherwise. Never override a manual selection.
    useEffect(() => {
        if (bookType === 'audiobook') return;
        let cancelled = false;
        (async () => {
            try {
                const s = await getSettings();
                if (!cancelled && !providerTouched.current && s.hardcover_configured) {
                    setProvider('hardcover');
                }
            } catch { /* keep the Google default */ }
        })();
        return () => { cancelled = true; };
    }, [bookType]);
    const [query, setQuery] = useState(currentData.title || currentData.isbn || '');
    const [author, setAuthor] = useState(currentData.author || '');
    const [loading, setLoading] = useState(false);
    const [results, setResults] = useState([]);
    const [error, setError] = useState('');

    // View state: 'search' | 'compare'
    const [view, setView] = useState('search');
    const [selectedResult, setSelectedResult] = useState(null);

    // Checkbox state for comparison view
    const [selectedFields, setSelectedFields] = useState(emptyFieldSelection());

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
        const defaults = {};
        for (const field of [...MATCH_FIELDS, 'description']) {
            defaults[field] = !hasValue(currentData[field]) && hasValue(result[field]);
        }
        defaults.cover_url = !currentData.cover_path && !!result.cover_url;
        setSelectedFields(defaults);

        setView('compare');
    };

    const handleFieldToggle = (field) => {
        setSelectedFields(prev => ({ ...prev, [field]: !prev[field] }));
    };

    const handleApplySelected = () => {
        const payload = {};
        for (const field of [...MATCH_FIELDS, 'description']) {
            if (selectedFields[field]) payload[field] = selectedResult[field];
        }
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
                                onChange={e => { providerTouched.current = true; setProvider(e.target.value); }}
                            >
                                <option value="google">Google Books</option>
                                <option value="openlibrary">Open Library</option>
                                <option value="audible">Audible</option>
                                <option value="hardcover">Hardcover</option>
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
                                    {res.series && (
                                        <p className="text-small">
                                            {res.series}{res.series_index != null ? ` #${res.series_index}` : ''}
                                        </p>
                                    )}
                                    <p className="text-small">
                                        {res.publish_year || 'Unknown Year'}
                                        {res.publisher ? ` · ${res.publisher}` : ''}
                                    </p>
                                    <button
                                        type="button"
                                        className="btn btn-primary btn-sm mt-auto"
                                        style={{ alignSelf: 'flex-start' }}
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
                        background: var(--bg-card-hover);
                        border: 1px solid var(--border);
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
                        {MATCH_FIELDS.map(field => (
                            <tr key={field}>
                                <td style={{ textAlign: 'center' }}>
                                    <input
                                        type="checkbox"
                                        checked={selectedFields[field]}
                                        onChange={() => handleFieldToggle(field)}
                                        disabled={!hasValue(selectedResult[field])}
                                    />
                                </td>
                                <td style={{ textTransform: 'capitalize' }}><strong>{field.replace('_', ' ')}</strong></td>
                                <td>{hasValue(currentData[field]) ? currentData[field] : <span className="text-muted">Empty</span>}</td>
                                <td>{hasValue(selectedResult[field]) ? selectedResult[field] : <span className="text-muted">Empty</span>}</td>
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
                        border-bottom: 1px solid var(--border);
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
