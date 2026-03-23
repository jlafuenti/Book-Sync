import React, { useState, useEffect, useCallback, useRef } from 'react'
import { getNewPairs, acknowledgeNewPairs, getMetadataDiscrepancies, resolveMetadataDiscrepancies, ignoreMetadataDiscrepancies, coverSrc } from '../api'
import { useAuth } from '../contexts/AuthContext'
import MetadataCleanupModal from '../components/MetadataCleanupModal'

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

    // Metadata cleanup modal
    const [showCleanupModal, setShowCleanupModal] = useState(false)

    // Multi-select + shift+click range selection
    const [selected, setSelected] = useState(new Set())
    const lastClickedRef = useRef(null)
    const pairShiftRef = useRef(false)  // captures shiftKey from onClick before onChange fires

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
            const map = {}
            discData.forEach(d => { map[d.pair_id] = d })
            setDiscrepancyMap(map)
            setSelected(new Set())
            setExpandedPairId(null)
            lastClickedRef.current = null
        } catch (e) {
            setError(e.message)
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => { load() }, [load])

    const cleanPairs = pairs.filter(p => !discrepancyMap[p.id] || discrepancyMap[p.id].discrepancies.length === 0)
    const mismatchPairs = pairs.filter(p => discrepancyMap[p.id]?.discrepancies.length > 0)
    const visiblePairs = filter === 'clean' ? cleanPairs : filter === 'mismatches' ? mismatchPairs : pairs
    const visibleIds = visiblePairs.map(p => p.id)

    const allVisibleSelected = visiblePairs.length > 0 && visiblePairs.every(p => selected.has(p.id))

    function toggleAllVisible() {
        if (allVisibleSelected) {
            setSelected(prev => {
                const next = new Set(prev)
                visiblePairs.forEach(p => next.delete(p.id))
                return next
            })
        } else {
            setSelected(prev => new Set([...prev, ...visibleIds]))
        }
        lastClickedRef.current = null
    }

    // shiftKey is captured via onClick ref before onChange fires
    function handleCheck(id, shiftKey) {
        if (shiftKey && lastClickedRef.current !== null) {
            const idx1 = visibleIds.indexOf(lastClickedRef.current)
            const idx2 = visibleIds.indexOf(id)
            if (idx1 !== -1 && idx2 !== -1) {
                const [start, end] = [Math.min(idx1, idx2), Math.max(idx1, idx2)]
                const rangeIds = visibleIds.slice(start, end + 1)
                const shouldCheck = !selected.has(id)
                setSelected(prev => {
                    const next = new Set(prev)
                    rangeIds.forEach(k => shouldCheck ? next.add(k) : next.delete(k))
                    return next
                })
                lastClickedRef.current = id
                return
            }
        }
        setSelected(prev => {
            const next = new Set(prev)
            if (next.has(id)) next.delete(id); else next.add(id)
            return next
        })
        lastClickedRef.current = id
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

    async function acknowledgeAllClean() {
        const ids = cleanPairs.map(p => p.id)
        if (ids.length === 0) return
        await acknowledgeNewPairs(ids)
        await load()
    }

    function toggleExpand(pairId) {
        setExpandedPairId(prev => prev === pairId ? null : pairId)
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
                    {/* Inline top controls: filter tabs, select-all, bulk actions */}
                    <div style={{ marginBottom: '1rem' }}>
                        {/* Filter tabs */}
                        <div style={{ display: 'flex', gap: '0.25rem', marginBottom: '0.75rem', flexWrap: 'wrap' }}>
                            {[
                                { key: 'all', label: `All (${pairs.length})` },
                                { key: 'mismatches', label: `⚠️ Has mismatches (${mismatchPairs.length})` },
                                { key: 'clean', label: `✓ No mismatches (${cleanPairs.length})` },
                            ].map(f => (
                                <button
                                    key={f.key}
                                    className={`btn btn-sm ${filter === f.key ? 'btn-primary' : 'btn-secondary'}`}
                                    onClick={() => { setFilter(f.key); setSelected(new Set()); lastClickedRef.current = null }}
                                >
                                    {f.label}
                                </button>
                            ))}
                            {filter === 'clean' && cleanPairs.length > 0 && canEdit && (
                                <button
                                    className="btn btn-sm btn-primary"
                                    onClick={acknowledgeAllClean}
                                    style={{ marginLeft: 'auto' }}
                                >
                                    Acknowledge all clean ({cleanPairs.length})
                                </button>
                            )}
                        </div>

                        {/* Select-all + right-side actions */}
                        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                            <label style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', cursor: 'pointer' }}>
                                <input type="checkbox" checked={allVisibleSelected} onChange={toggleAllVisible} />
                                Select all visible ({visiblePairs.length})
                            </label>
                            <div style={{ marginLeft: 'auto', display: 'flex', gap: '0.5rem' }}>
                                {canEdit && mismatchPairs.length > 0 && (
                                    <button className="btn btn-secondary" onClick={() => setShowCleanupModal(true)}>
                                        🧹 Clean Up Metadata
                                    </button>
                                )}
                                {canEdit && (
                                    <button className="btn btn-secondary" onClick={acknowledgeAll}>
                                        Acknowledge all
                                    </button>
                                )}
                            </div>
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
                                        <input
                                            type="checkbox"
                                            checked={selected.has(pair.id)}
                                            onClick={(e) => { pairShiftRef.current = e.shiftKey }}
                                            onChange={() => handleCheck(pair.id, pairShiftRef.current)}
                                        />

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
                                                        <th style={{ textAlign: 'left', paddingBottom: '0.4rem' }}>Ebook value</th>
                                                        <th style={{ textAlign: 'left', paddingBottom: '0.4rem' }}>Audiobook value</th>
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
                                                                                ? <img src={coverSrc(d.ebook_value)} alt="Ebook cover" style={{ height: 80, borderRadius: 4, objectFit: 'cover' }} />
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
                                                                                ? <img src={coverSrc(d.audiobook_value)} alt="Audiobook cover" style={{ height: 80, borderRadius: 4, objectFit: 'cover' }} />
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

            {/* Floating bottom bar — appears when anything is selected */}
            {selected.size > 0 && canEdit && (
                <div style={{
                    position: 'fixed', bottom: '24px', left: '50%', transform: 'translateX(-50%)',
                    background: 'var(--bg-secondary)', border: '1px solid var(--border)',
                    borderRadius: '12px', padding: '12px 20px',
                    display: 'flex', alignItems: 'center', gap: '12px',
                    boxShadow: '0 8px 32px rgba(0,0,0,0.7)', zIndex: 100,
                    whiteSpace: 'nowrap',
                }}>
                    <span style={{ fontWeight: 600, color: 'var(--text-secondary)' }}>
                        {selected.size} selected
                    </span>
                    <button className="btn btn-primary" onClick={acknowledgeSelected}>Acknowledge selected</button>
                    <button className="btn btn-secondary" onClick={() => { setSelected(new Set()); lastClickedRef.current = null }}>Cancel</button>
                </div>
            )}

            {showCleanupModal && (
                <MetadataCleanupModal
                    onClose={() => setShowCleanupModal(false)}
                    onComplete={() => {
                        setShowCleanupModal(false)
                        load()
                    }}
                />
            )}
        </div>
    )
}
