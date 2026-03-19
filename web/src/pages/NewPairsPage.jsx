import React, { useState, useEffect, useCallback } from 'react'
import { getNewPairs, acknowledgeNewPairs, getMetadataDiscrepancies, resolveMetadataDiscrepancies, ignoreMetadataDiscrepancies } from '../api'
import { useAuth } from '../contexts/AuthContext'

const FIELDS_LABEL = {
    title: 'Title',
    author: 'Author',
    series: 'Series',
    series_index: 'Series #',
    description: 'Description',
    publisher: 'Publisher',
    publish_year: 'Year',
    language: 'Language',
    genres: 'Genres',
    tags: 'Tags',
    is_explicit: 'Explicit',
    is_abridged: 'Abridged',
    cover_path: 'Cover',
}

/**
 * New Pairs inbox — shows unacknowledged book pairs.
 * Pairs disappear once all metadata mismatches are resolved/skipped,
 * or once manually acknowledged.
 */
export default function NewPairsPage() {
    const { hasMinRole } = useAuth()
    const canEdit = hasMinRole('editor')

    const [pairs, setPairs] = useState([])
    const [discrepancyMap, setDiscrepancyMap] = useState({})  // pairId → [fields]
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)

    // Filter: 'all' | 'mismatches' | 'clean'
    const [filter, setFilter] = useState('all')

    // Multi-select
    const [selected, setSelected] = useState(new Set())

    // Inline resolve state: pairId → { field → 'ebook'|'audiobook'|null }
    const [resolveState, setResolveState] = useState({})
    const [expandedPairId, setExpandedPairId] = useState(null)
    const [saving, setSaving] = useState(false)

    const load = useCallback(async () => {
        try {
            setLoading(true)
            const [pairsData, discData] = await Promise.all([
                getNewPairs(),
                getMetadataDiscrepancies(),
            ])
            setPairs(pairsData)
            // Build a map from pairId → discrepancy fields
            const map = {}
            discData.forEach(d => { map[d.pair_id] = d })
            setDiscrepancyMap(map)
            setSelected(new Set())
            setExpandedPairId(null)
        } catch (e) {
            setError(e.message)
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => { load() }, [load])

    function toggle(id) {
        setSelected(prev => {
            const next = new Set(prev)
            if (next.has(id)) next.delete(id)
            else next.add(id)
            return next
        })
    }

    async function acknowledgeSelected() {
        await acknowledgeNewPairs([...selected])
        await load()
    }

    async function acknowledgeAll() {
        await acknowledgeNewPairs(pairs.map(p => p.id))
        await load()
    }

    async function acknowledgeSingle(pairId) {
        await acknowledgeNewPairs([pairId])
        await load()
    }

    function toggleExpand(pairId) {
        setExpandedPairId(prev => prev === pairId ? null : pairId)
        // Initialize resolve state for this pair if needed
        setResolveState(prev => {
            if (prev[pairId]) return prev
            return { ...prev, [pairId]: {} }
        })
    }

    function setFieldChoice(pairId, field, source) {
        setResolveState(prev => ({
            ...prev,
            [pairId]: {
                ...prev[pairId],
                [field]: prev[pairId]?.[field] === source ? null : source,
            }
        }))
    }

    async function saveResolutions(pairId) {
        const disc = discrepancyMap[pairId]
        if (!disc) return
        const choices = resolveState[pairId] || {}
        const ebook_updates = {}
        const audiobook_updates = {}
        disc.discrepancies.forEach(d => {
            const choice = choices[d.field]
            if (choice === 'ebook') audiobook_updates[d.field] = d.ebook_value
            else if (choice === 'audiobook') ebook_updates[d.field] = d.audiobook_value
        })
        setSaving(true)
        try {
            await resolveMetadataDiscrepancies(pairId, { ebook_updates, audiobook_updates })
            await load()
        } finally {
            setSaving(false)
        }
    }

    async function skipAllMismatches(pairId) {
        const disc = discrepancyMap[pairId]
        if (!disc) {
            await acknowledgeNewPairs([pairId])
        } else {
            const fields = disc.discrepancies.map(d => d.field)
            await ignoreMetadataDiscrepancies(pairId, fields)
        }
        await load()
    }

    const cleanPairs = pairs.filter(p => !discrepancyMap[p.id] || discrepancyMap[p.id].discrepancies.length === 0)
    const mismatchPairs = pairs.filter(p => discrepancyMap[p.id]?.discrepancies.length > 0)
    const visiblePairs = filter === 'clean' ? cleanPairs : filter === 'mismatches' ? mismatchPairs : pairs

    const allVisibleSelected = visiblePairs.length > 0 && visiblePairs.every(p => selected.has(p.id))

    function toggleAllVisible() {
        if (allVisibleSelected) {
            setSelected(prev => {
                const next = new Set(prev)
                visiblePairs.forEach(p => next.delete(p.id))
                return next
            })
        } else {
            setSelected(prev => new Set([...prev, ...visiblePairs.map(p => p.id)]))
        }
    }

    async function acknowledgeAllClean() {
        const ids = cleanPairs.map(p => p.id)
        if (ids.length === 0) return
        await acknowledgeNewPairs(ids)
        await load()
    }

    function formatDate(dt) {
        if (!dt) return '—'
        return new Date(dt).toLocaleDateString()
    }

    if (loading) return <div className="page-content"><p>Loading new pairs…</p></div>
    if (error) return <div className="page-content"><p className="error">{error}</p></div>

    return (
        <div className="page-content">
            <div className="page-header">
                <h2>New Pairs <span className="badge">{pairs.length}</span></h2>
                <p className="page-subtitle">
                    Newly created book pairs awaiting review. Verify filenames match titles and resolve any metadata discrepancies.
                </p>
            </div>

            {pairs.length === 0 ? (
                <div className="empty-state">
                    <p>✅ No new pairs — you're all caught up!</p>
                </div>
            ) : (
                <>
                    {/* Sticky filter + bulk actions */}
                    <div style={{
                        position: 'sticky', top: 0, zIndex: 10,
                        background: 'var(--background, #fff)',
                        paddingTop: '0.5rem',
                        paddingBottom: '0.5rem',
                        marginBottom: '0.75rem',
                        borderBottom: '1px solid var(--border)',
                    }}>
                        {/* Filter tabs */}
                        <div style={{ display: 'flex', gap: '0.25rem', marginBottom: '0.5rem', flexWrap: 'wrap' }}>
                            {[
                                { key: 'all', label: `All (${pairs.length})` },
                                { key: 'mismatches', label: `⚠️ Has mismatches (${mismatchPairs.length})` },
                                { key: 'clean', label: `✓ No mismatches (${cleanPairs.length})` },
                            ].map(f => (
                                <button
                                    key={f.key}
                                    className={`btn btn-sm ${filter === f.key ? 'btn-primary' : 'btn-secondary'}`}
                                    onClick={() => { setFilter(f.key); setSelected(new Set()) }}
                                >
                                    {f.label}
                                </button>
                            ))}
                            {filter === 'clean' && cleanPairs.length > 0 && canEdit && (
                                <button
                                    className="btn btn-sm btn-primary"
                                    onClick={acknowledgeAllClean}
                                    style={{ marginLeft: 'auto' }}
                                    title="Acknowledge all pairs with no metadata mismatches"
                                >
                                    Acknowledge all clean ({cleanPairs.length})
                                </button>
                            )}
                        </div>

                        {/* Bulk actions */}
                        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                            <label style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', cursor: 'pointer' }}>
                                <input type="checkbox" checked={allVisibleSelected} onChange={toggleAllVisible} />
                                Select all visible ({visiblePairs.length})
                            </label>
                            {selected.size > 0 && canEdit && (
                                <button className="btn btn-secondary" onClick={acknowledgeSelected}>
                                    Acknowledge selected ({selected.size})
                                </button>
                            )}
                            {canEdit && pairs.length > 0 && (
                                <button className="btn btn-secondary" onClick={acknowledgeAll} style={{ marginLeft: 'auto' }}>
                                    Acknowledge all
                                </button>
                            )}
                        </div>
                    </div>

                    {visiblePairs.length === 0 && (
                        <div className="empty-state"><p>No pairs match this filter.</p></div>
                    )}

                    {/* Pairs list */}
                    <div className="pairs-inbox">
                        {visiblePairs.map(pair => {
                            const disc = discrepancyMap[pair.id]
                            const hasDisc = disc && disc.discrepancies.length > 0
                            const isExpanded = expandedPairId === pair.id
                            const choices = resolveState[pair.id] || {}
                            const anyChosen = hasDisc && disc.discrepancies.some(d => choices[d.field])

                            return (
                                <div
                                    key={pair.id}
                                    className={`pair-inbox-row ${selected.has(pair.id) ? 'row-selected' : ''}`}
                                    style={{
                                        border: '1px solid var(--border)',
                                        borderRadius: 6,
                                        marginBottom: '0.75rem',
                                        overflow: 'hidden',
                                    }}
                                >
                                    {/* Row header */}
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', padding: '0.75rem 1rem', background: 'var(--surface)' }}>
                                        <input type="checkbox" checked={selected.has(pair.id)} onChange={() => toggle(pair.id)} />

                                        <div style={{ flex: 1, minWidth: 0 }}>
                                            <div style={{ fontWeight: 600 }}>{pair.ebook?.title || pair.audiobook?.title || 'Unknown'}</div>
                                            <div style={{ fontSize: '0.82em', color: 'var(--text-muted)', marginTop: 2 }}>
                                                <span title="Ebook filename">📄 {pair.ebook?.filename}</span>
                                                <span style={{ margin: '0 0.5rem' }}>·</span>
                                                <span title="Audiobook filename">🎧 {pair.audiobook?.filename}</span>
                                                <span style={{ margin: '0 0.5rem' }}>·</span>
                                                <span>{formatDate(pair.matched_at)}</span>
                                            </div>
                                        </div>

                                        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexShrink: 0 }}>
                                            {hasDisc && (
                                                <span className="badge badge-warning" title={`${disc.discrepancies.length} metadata mismatch(es)`}>
                                                    ⚠️ {disc.discrepancies.length} mismatch{disc.discrepancies.length > 1 ? 'es' : ''}
                                                </span>
                                            )}
                                            {!hasDisc && (
                                                <span className="badge badge-success">✓ Metadata OK</span>
                                            )}

                                            {hasDisc && canEdit && (
                                                <button
                                                    className="btn btn-sm btn-secondary"
                                                    onClick={() => toggleExpand(pair.id)}
                                                >
                                                    {isExpanded ? 'Collapse' : 'Resolve'}
                                                </button>
                                            )}
                                            {hasDisc && canEdit && (
                                                <button
                                                    className="btn btn-sm btn-secondary"
                                                    onClick={() => skipAllMismatches(pair.id)}
                                                    title="Skip all mismatches for this pair"
                                                >
                                                    Skip all
                                                </button>
                                            )}
                                            {canEdit && (
                                                <button
                                                    className="btn btn-sm btn-primary"
                                                    onClick={() => acknowledgeSingle(pair.id)}
                                                >
                                                    Acknowledge
                                                </button>
                                            )}
                                        </div>
                                    </div>

                                    {/* Inline discrepancy resolver */}
                                    {isExpanded && hasDisc && (
                                        <div style={{ padding: '1rem', borderTop: '1px solid var(--border)', background: 'var(--bg)' }}>
                                            <table style={{ width: '100%', fontSize: '0.9em' }}>
                                                <thead>
                                                    <tr>
                                                        <th style={{ textAlign: 'left', paddingBottom: '0.4rem' }}>Field</th>
                                                        <th style={{ textAlign: 'left', paddingBottom: '0.4rem' }}>
                                                            <label style={{ display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
                                                                Ebook value
                                                            </label>
                                                        </th>
                                                        <th style={{ textAlign: 'left', paddingBottom: '0.4rem' }}>
                                                            Audiobook value
                                                        </th>
                                                    </tr>
                                                </thead>
                                                <tbody>
                                                    {disc.discrepancies.map(d => {
                                                        const isCover = d.field === 'cover_path'
                                                        return (
                                                        <tr key={d.field}>
                                                            <td style={{ paddingRight: '1rem', paddingTop: '0.5rem', whiteSpace: 'nowrap', color: 'var(--text-muted)', verticalAlign: 'top' }}>
                                                                {FIELDS_LABEL[d.field] || d.field}
                                                            </td>
                                                            <td style={{ paddingRight: '1rem', paddingTop: '0.5rem', verticalAlign: 'top' }}>
                                                                <label style={{ display: 'flex', alignItems: 'flex-start', gap: 6, cursor: 'pointer' }}>
                                                                    <input
                                                                        type="radio"
                                                                        name={`${pair.id}-${d.field}`}
                                                                        checked={choices[d.field] === 'ebook'}
                                                                        onChange={() => setFieldChoice(pair.id, d.field, 'ebook')}
                                                                        style={{ marginTop: isCover ? 0 : 3, flexShrink: 0 }}
                                                                    />
                                                                    {isCover
                                                                        ? (d.ebook_value
                                                                            ? <img src={d.ebook_value} alt="Ebook cover" style={{ height: 80, borderRadius: 4, objectFit: 'cover' }} />
                                                                            : <em style={{ color: 'var(--text-muted)' }}>No cover</em>)
                                                                        : <span style={{ wordBreak: 'break-word' }}>{d.ebook_value ?? <em style={{ color: 'var(--text-muted)' }}>(empty)</em>}</span>
                                                                    }
                                                                </label>
                                                            </td>
                                                            <td style={{ paddingTop: '0.5rem', verticalAlign: 'top' }}>
                                                                <label style={{ display: 'flex', alignItems: 'flex-start', gap: 6, cursor: 'pointer' }}>
                                                                    <input
                                                                        type="radio"
                                                                        name={`${pair.id}-${d.field}`}
                                                                        checked={choices[d.field] === 'audiobook'}
                                                                        onChange={() => setFieldChoice(pair.id, d.field, 'audiobook')}
                                                                        style={{ marginTop: isCover ? 0 : 3, flexShrink: 0 }}
                                                                    />
                                                                    {isCover
                                                                        ? (d.audiobook_value
                                                                            ? <img src={d.audiobook_value} alt="Audiobook cover" style={{ height: 80, borderRadius: 4, objectFit: 'cover' }} />
                                                                            : <em style={{ color: 'var(--text-muted)' }}>No cover</em>)
                                                                        : <span style={{ wordBreak: 'break-word' }}>{d.audiobook_value ?? <em style={{ color: 'var(--text-muted)' }}>(empty)</em>}</span>
                                                                    }
                                                                </label>
                                                            </td>
                                                        </tr>
                                                        )
                                                    })}
                                                </tbody>
                                            </table>
                                            <div style={{ marginTop: '0.75rem', display: 'flex', gap: '0.5rem' }}>
                                                <button
                                                    className="btn btn-primary btn-sm"
                                                    onClick={() => saveResolutions(pair.id)}
                                                    disabled={!anyChosen || saving}
                                                    title="Save whichever fields you've chosen — unselected fields stay as mismatches"
                                                >
                                                    {saving ? 'Saving…' : 'Save selected resolutions'}
                                                </button>
                                                <button
                                                    className="btn btn-secondary btn-sm"
                                                    onClick={() => skipAllMismatches(pair.id)}
                                                    disabled={saving}
                                                >
                                                    Skip all mismatches
                                                </button>
                                            </div>
                                        </div>
                                    )}
                                </div>
                            )
                        })}
                    </div>
                </>
            )}
        </div>
    )
}
