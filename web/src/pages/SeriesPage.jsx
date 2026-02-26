import React, { useState, useEffect, useMemo } from 'react'
import { getEbooks, getAudiobooks, getPairs, createPair } from '../api'

/**
 * SeriesPage — Audiobookshelf-inspired series-first library view.
 * Groups all ebooks, audiobooks, and pairs by series name into collapsible cards.
 */
export default function SeriesPage() {
    const [ebooks, setEbooks] = useState([])
    const [audiobooks, setAudiobooks] = useState([])
    const [pairs, setPairs] = useState([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState('')
    const [search, setSearch] = useState('')
    const [sortBy, setSortBy] = useState('name') // 'name' | 'count' | 'recent'
    const [expandedSeries, setExpandedSeries] = useState(new Set())

    useEffect(() => {
        loadAll()
    }, [])

    async function loadAll() {
        setLoading(true)
        setError('')
        try {
            const [e, a, p] = await Promise.all([getEbooks(), getAudiobooks(), getPairs()])
            setEbooks(e)
            setAudiobooks(a)
            setPairs(p)
        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }

    // Build series groups
    const { seriesGroups, unseriedItems } = useMemo(() => {
        const pairedEbookIds = new Set(pairs.map(p => p.ebook.id))
        const pairedAudiobookIds = new Set(pairs.map(p => p.audiobook.id))

        // Collect all items with a unified shape
        const allItems = []

        // Pairs
        pairs.forEach(p => {
            const series = p.ebook.series || p.audiobook.series
            const seriesIndex = p.ebook.series_index ?? p.audiobook.series_index
            allItems.push({
                key: `pair_${p.id}`,
                type: 'pair',
                title: p.ebook.title,
                author: p.ebook.author || p.audiobook.author,
                series,
                seriesIndex,
                hasEbook: true,
                hasAudiobook: true,
                ebookFormat: p.ebook.format,
                audiobookFormat: p.audiobook.format,
                status: p.status,
                uploadedAt: p.ebook.uploaded_at,
                ebookId: p.ebook.id,
                audiobookId: p.audiobook.id,
                pairId: p.id,
            })
        })

        // Standalone ebooks
        ebooks.filter(e => !pairedEbookIds.has(e.id)).forEach(e => {
            allItems.push({
                key: `ebook_${e.id}`,
                type: 'ebook',
                title: e.title,
                author: e.author,
                series: e.series,
                seriesIndex: e.series_index,
                hasEbook: true,
                hasAudiobook: false,
                ebookFormat: e.format,
                uploadedAt: e.uploaded_at,
                ebookId: e.id,
            })
        })

        // Standalone audiobooks
        audiobooks.filter(a => !pairedAudiobookIds.has(a.id)).forEach(a => {
            allItems.push({
                key: `audio_${a.id}`,
                type: 'audiobook',
                title: a.title,
                author: a.author,
                series: a.series,
                seriesIndex: a.series_index,
                hasEbook: false,
                hasAudiobook: true,
                audiobookFormat: a.format,
                uploadedAt: a.uploaded_at,
                audiobookId: a.id,
            })
        })

        // Group by series
        const groups = {}
        const unsorted = []

        allItems.forEach(item => {
            if (item.series) {
                if (!groups[item.series]) {
                    groups[item.series] = {
                        name: item.series,
                        author: item.author,
                        items: [],
                        latestUpload: item.uploadedAt,
                    }
                }
                groups[item.series].items.push(item)
                // Track latest upload for "Recently Added" sort
                if (item.uploadedAt > groups[item.series].latestUpload) {
                    groups[item.series].latestUpload = item.uploadedAt
                }
                // Use most common author
                if (!groups[item.series].author && item.author) {
                    groups[item.series].author = item.author
                }
            } else {
                unsorted.push(item)
            }
        })

        // Sort items within each series by series_index
        Object.values(groups).forEach(g => {
            g.items.sort((a, b) => (a.seriesIndex ?? 999) - (b.seriesIndex ?? 999))
        })

        return { seriesGroups: Object.values(groups), unseriedItems: unsorted }
    }, [ebooks, audiobooks, pairs])

    // Filter & sort series
    const filteredSeries = useMemo(() => {
        const q = search.toLowerCase()
        let filtered = seriesGroups.filter(g => {
            if (!q) return true
            if (g.name.toLowerCase().includes(q)) return true
            if (g.author && g.author.toLowerCase().includes(q)) return true
            if (g.items.some(i => i.title.toLowerCase().includes(q))) return true
            return false
        })

        if (sortBy === 'name') {
            const prefixRe = /^(the|a|an)\s+/i
            filtered.sort((a, b) => a.name.replace(prefixRe, '').localeCompare(b.name.replace(prefixRe, '')))
        } else if (sortBy === 'count') {
            filtered.sort((a, b) => b.items.length - a.items.length)
        } else if (sortBy === 'recent') {
            filtered.sort((a, b) => (b.latestUpload || '').localeCompare(a.latestUpload || ''))
        }

        return filtered
    }, [seriesGroups, search, sortBy])

    // Filtered unsorted items
    const filteredUnsorted = useMemo(() => {
        const q = search.toLowerCase()
        if (!q) return unseriedItems
        return unseriedItems.filter(i =>
            i.title.toLowerCase().includes(q) ||
            (i.author && i.author.toLowerCase().includes(q))
        )
    }, [unseriedItems, search])

    function toggleSeries(name) {
        setExpandedSeries(prev => {
            const next = new Set(prev)
            if (next.has(name)) next.delete(name)
            else next.add(name)
            return next
        })
    }

    function expandAll() {
        setExpandedSeries(new Set(filteredSeries.map(g => g.name)))
    }

    function collapseAll() {
        setExpandedSeries(new Set())
    }

    if (loading) {
        return (
            <div className="page-header">
                <h2>📖 Series</h2>
                <p>Loading library...</p>
                <div style={{ marginTop: 24 }}>
                    <div className="spinner" />
                </div>
            </div>
        )
    }

    return (
        <div>
            <div className="page-header">
                <h2>📖 Series</h2>
                <p>Browse your library organized by series — {filteredSeries.length} series, {filteredSeries.reduce((a, g) => a + g.items.length, 0)} items</p>
            </div>

            {error && <div className="alert alert-error" style={{ marginBottom: 16 }}>{error}</div>}

            {/* Toolbar */}
            <div style={{ display: 'flex', gap: 12, marginBottom: 24, flexWrap: 'wrap', alignItems: 'center' }}>
                <input
                    type="text"
                    value={search}
                    onChange={e => setSearch(e.target.value)}
                    placeholder="Search series, author, or title..."
                    className="form-control"
                    style={{ flex: 1, minWidth: 200 }}
                />
                <select
                    value={sortBy}
                    onChange={e => setSortBy(e.target.value)}
                    className="form-control"
                    style={{ width: 'auto', minWidth: 140 }}
                >
                    <option value="name">A → Z</option>
                    <option value="count">Most Books</option>
                    <option value="recent">Recently Added</option>
                </select>
                <button className="btn btn-secondary btn-sm" onClick={expandAll}>Expand All</button>
                <button className="btn btn-secondary btn-sm" onClick={collapseAll}>Collapse All</button>
            </div>

            {/* Series Cards */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                {filteredSeries.map(group => (
                    <SeriesCard
                        key={group.name}
                        group={group}
                        expanded={expandedSeries.has(group.name)}
                        onToggle={() => toggleSeries(group.name)}
                    />
                ))}
            </div>

            {/* Unsorted section */}
            {filteredUnsorted.length > 0 && (
                <div style={{ marginTop: 32 }}>
                    <div style={{
                        display: 'flex', alignItems: 'center', gap: 8,
                        marginBottom: 12, color: 'var(--text-secondary)'
                    }}>
                        <span style={{ fontSize: '1.1rem', fontWeight: 600 }}>📁 No Series</span>
                        <span className="badge" style={{ background: 'var(--bg-card)', color: 'var(--text-muted)', fontSize: '0.75rem', padding: '2px 8px', borderRadius: 12 }}>
                            {filteredUnsorted.length}
                        </span>
                    </div>
                    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
                        <table className="table" style={{ width: '100%' }}>
                            <tbody>
                                {filteredUnsorted.map(item => (
                                    <ItemRow key={item.key} item={item} />
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}

            {filteredSeries.length === 0 && filteredUnsorted.length === 0 && !loading && (
                <div style={{ textAlign: 'center', padding: '48px 0', color: 'var(--text-muted)' }}>
                    <div style={{ fontSize: '3rem', marginBottom: 12 }}>📚</div>
                    <div style={{ fontSize: '1.1rem' }}>No series found{search ? ` matching "${search}"` : ''}</div>
                </div>
            )}
        </div>
    )
}

function SeriesCard({ group, expanded, onToggle }) {
    const itemCount = group.items.length
    const pairedCount = group.items.filter(i => i.type === 'pair').length
    const ebookOnlyCount = group.items.filter(i => i.type === 'ebook').length
    const audioOnlyCount = group.items.filter(i => i.type === 'audiobook').length

    return (
        <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
            {/* Header */}
            <div
                onClick={onToggle}
                style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    padding: '16px 20px',
                    cursor: 'pointer',
                    userSelect: 'none',
                    transition: 'var(--transition)',
                    background: expanded ? 'var(--accent-light)' : 'transparent',
                }}
            >
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                    <span style={{
                        transform: expanded ? 'rotate(90deg)' : 'rotate(0deg)',
                        transition: 'transform 0.2s ease',
                        display: 'inline-block',
                        fontSize: '0.9rem',
                        color: 'var(--text-muted)',
                    }}>▶</span>
                    <div>
                        <div style={{ fontWeight: 600, fontSize: '1rem' }}>
                            {group.name}
                        </div>
                        {group.author && (
                            <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: 2 }}>
                                {group.author}
                            </div>
                        )}
                    </div>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span className="badge" style={{
                        background: 'var(--accent-light)',
                        color: 'var(--accent)',
                        padding: '3px 10px',
                        borderRadius: 12,
                        fontSize: '0.75rem',
                        fontWeight: 600,
                    }}>
                        {itemCount} {itemCount === 1 ? 'book' : 'books'}
                    </span>
                    {pairedCount > 0 && (
                        <span style={{ fontSize: '0.75rem', color: 'var(--success)' }} title={`${pairedCount} paired`}>
                            🔗 {pairedCount}
                        </span>
                    )}
                    {ebookOnlyCount > 0 && (
                        <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }} title={`${ebookOnlyCount} ebook only`}>
                            📚 {ebookOnlyCount}
                        </span>
                    )}
                    {audioOnlyCount > 0 && (
                        <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }} title={`${audioOnlyCount} audiobook only`}>
                            🎧 {audioOnlyCount}
                        </span>
                    )}
                </div>
            </div>

            {/* Body */}
            {expanded && (
                <div style={{ borderTop: '1px solid var(--border)' }}>
                    <table className="table" style={{ width: '100%' }}>
                        <tbody>
                            {group.items.map(item => (
                                <ItemRow key={item.key} item={item} showIndex />
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    )
}

function ItemRow({ item, showIndex }) {
    const indexStr = item.seriesIndex != null
        ? (item.seriesIndex % 1 === 0 ? `#${Math.floor(item.seriesIndex)}` : `#${item.seriesIndex}`)
        : null

    return (
        <tr style={{ borderBottom: '1px solid var(--border)' }}>
            {showIndex && (
                <td style={{ padding: '10px 12px', width: 48, textAlign: 'center' }}>
                    {indexStr && (
                        <span style={{
                            background: 'var(--accent-light)',
                            color: 'var(--accent)',
                            padding: '2px 8px',
                            borderRadius: 8,
                            fontSize: '0.75rem',
                            fontWeight: 600,
                        }}>{indexStr}</span>
                    )}
                </td>
            )}
            <td style={{ padding: '10px 12px' }}>
                <div style={{ fontWeight: 500, fontSize: '0.9rem' }}>{item.title}</div>
                {item.author && (
                    <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{item.author}</div>
                )}
            </td>
            <td style={{ padding: '10px 12px', textAlign: 'right' }}>
                <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end', alignItems: 'center' }}>
                    {item.hasEbook && (
                        <span style={{
                            background: 'var(--info-bg)',
                            color: 'var(--info)',
                            padding: '2px 8px',
                            borderRadius: 8,
                            fontSize: '0.7rem',
                            fontWeight: 600,
                        }}>📚 {item.ebookFormat?.toUpperCase()}</span>
                    )}
                    {item.hasAudiobook && (
                        <span style={{
                            background: 'var(--warning-bg)',
                            color: 'var(--warning)',
                            padding: '2px 8px',
                            borderRadius: 8,
                            fontSize: '0.7rem',
                            fontWeight: 600,
                        }}>🎧 {item.audiobookFormat?.toUpperCase()}</span>
                    )}
                    {item.type === 'pair' && (
                        <span style={{
                            background: 'var(--success-bg)',
                            color: 'var(--success)',
                            padding: '2px 8px',
                            borderRadius: 8,
                            fontSize: '0.7rem',
                            fontWeight: 600,
                        }}>
                            {item.status === 'synced' ? '✅ Synced' : '🔗 Paired'}
                        </span>
                    )}
                    {item.type !== 'pair' && (
                        <span style={{
                            background: 'var(--error-bg)',
                            color: 'var(--error)',
                            padding: '2px 8px',
                            borderRadius: 8,
                            fontSize: '0.7rem',
                            fontWeight: 600,
                        }}>Unpaired</span>
                    )}
                </div>
            </td>
        </tr>
    )
}
