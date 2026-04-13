import React, { useState, useEffect, useRef, useMemo } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import {
    getPairs, coverSrc,
    startTranscription, getTranscriptionStatus, cancelTranscription, addToQueue,
    getTranscriptionQueue, getQueueHistory, removeFromQueue, updateQueuePriority,
} from '../api'
import { useAuth } from '../contexts/AuthContext'
import useIsMobile from '../hooks/useIsMobile'
import FilterPill from '../components/FilterPill'
import './TranscriptionPage.css'

/* ── Helpers ─────────────────────────────────────────────────────────────── */

function formatDate(dateStr) {
    if (!dateStr) return '—'
    const s = dateStr.endsWith('Z') ? dateStr : `${dateStr}Z`
    return new Date(s).toLocaleString()
}

function formatDuration(startStr, endStr) {
    if (!startStr || !endStr) return '—'
    const start = new Date(startStr.endsWith('Z') ? startStr : `${startStr}Z`)
    const end   = new Date(endStr.endsWith('Z')   ? endStr   : `${endStr}Z`)
    const diffMs = end - start
    if (diffMs < 0) return '—'
    const totalSec = Math.floor(diffMs / 1000)
    const hours = Math.floor(totalSec / 3600)
    const mins  = Math.floor((totalSec % 3600) / 60)
    const secs  = totalSec % 60
    if (hours > 0) return `${hours}h ${mins}m ${secs}s`
    if (mins  > 0) return `${mins}m ${secs}s`
    return `${secs}s`
}

/* ── Icons ───────────────────────────────────────────────────────────────── */
const GridIcon = () => (
    <svg viewBox="0 0 24 24" fill="currentColor" width="16" height="16">
        <rect x="3" y="3" width="7" height="7" rx="1" />
        <rect x="14" y="3" width="7" height="7" rx="1" />
        <rect x="3" y="14" width="7" height="7" rx="1" />
        <rect x="14" y="14" width="7" height="7" rx="1" />
    </svg>
)
const ListIcon = () => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="16" height="16">
        <line x1="8" y1="6" x2="21" y2="6" /><line x1="8" y1="12" x2="21" y2="12" /><line x1="8" y1="18" x2="21" y2="18" />
        <line x1="3" y1="6" x2="3.01" y2="6" /><line x1="3" y1="12" x2="3.01" y2="12" /><line x1="3" y1="18" x2="3.01" y2="18" />
    </svg>
)
const ChevronIcon = ({ open }) => (
    <svg
        className={`transcription-series-chevron${open ? ' open' : ''}`}
        viewBox="0 0 24 24" fill="none" stroke="currentColor"
        strokeWidth="2" width="14" height="14"
    >
        <polyline points="9 18 15 12 9 6" />
    </svg>
)

/* ── Sort Pill ───────────────────────────────────────────────────────────── */
const SORT_LABELS = { title: 'Title', author: 'Author', series: 'Series' }

function SortPill({ sortField, setSortField, sortDir, setSortDir }) {
    const [open, setOpen] = useState(false)
    const ref = useRef(null)
    useEffect(() => {
        const h = e => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
        document.addEventListener('mousedown', h)
        return () => document.removeEventListener('mousedown', h)
    }, [])
    const dirLabel = sortDir === 'asc' ? '↑' : '↓'
    return (
        <div className="transcription-sort-wrap" ref={ref}>
            <button
                className={`transcription-sort-btn${open ? ' open' : ''}`}
                onClick={() => setOpen(o => !o)}
            >
                Sort: {SORT_LABELS[sortField]} {dirLabel}
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="12" height="12">
                    <polyline points="6 9 12 15 18 9" />
                </svg>
            </button>
            {open && (
                <div className="transcription-sort-dropdown">
                    {Object.entries(SORT_LABELS).map(([key, label]) => (
                        <button
                            key={key}
                            className={`transcription-sort-option${sortField === key ? ' active' : ''}`}
                            onClick={() => { setSortField(key); setOpen(false) }}
                        >{label}</button>
                    ))}
                    <div className="transcription-sort-divider" />
                    <button
                        className={`transcription-sort-option${sortDir === 'asc' ? ' active' : ''}`}
                        onClick={() => { setSortDir('asc'); setOpen(false) }}
                    >A → Z ↑</button>
                    <button
                        className={`transcription-sort-option${sortDir === 'desc' ? ' active' : ''}`}
                        onClick={() => { setSortDir('desc'); setOpen(false) }}
                    >Z → A ↓</button>
                </div>
            )}
        </div>
    )
}

/* ── Main Component ──────────────────────────────────────────────────────── */
function TranscriptionPage({ tab }) {
    const { hasMinRole } = useAuth()
    const canManageQueue = hasMinRole('admin')
    const isMobile = useIsMobile()

    // Mobile collapsible sections
    const [mobileSections, setMobileSections] = useState({
        'in-progress': true,
        queue: false,
        'not-transcribed': false,
        transcribed: false,
    })
    const toggleMobileSection = (key) => setMobileSections(prev => ({ ...prev, [key]: !prev[key] }))
    const [mobileShowAllNT, setMobileShowAllNT] = useState(false)
    const [mobileShowAllTx, setMobileShowAllTx] = useState(false)
    const [mobileSearch, setMobileSearch] = useState('')

    const [searchParams, setSearchParams] = useSearchParams()
    const searchQuery = searchParams.get('search') || ''

    /* ── pairs state ── */
    const [pairs,          setPairs]          = useState([])
    const [loading,        setLoading]        = useState(true)
    const [error,          setError]          = useState('')
    const [statuses,       setStatuses]       = useState({})
    const [expandedSeries, setExpandedSeries] = useState(new Set())
    const [activeTab,      setActiveTab]      = useState(tab || 'not-transcribed')

    /* ── per-tab view modes (all default to grid/visual) ── */
    const [tabViewModes, setTabViewModes] = useState({
        'not-transcribed': 'grid',
        queue:             'grid',
        'in-progress':     'grid',
        transcribed:       'grid',
    })

    /* ── filters & sort ── */
    const [authorFilter, setAuthorFilter] = useState('')
    const [seriesFilter, setSeriesFilter] = useState('')
    const [sortField,    setSortField]    = useState('title')
    const [sortDir,      setSortDir]      = useState('asc')

    /* ── queue state ── */
    const [queue,          setQueue]          = useState([])
    const [queueHistory,   setQueueHistory]   = useState([])
    const [historyLoaded,  setHistoryLoaded]  = useState(false)
    const [historyLoading, setHistoryLoading] = useState(false)
    const [showHistory,    setShowHistory]    = useState(false)
    const [historySortCol, setHistorySortCol] = useState('started_at')
    const [historySortDir, setHistorySortDir] = useState('desc')

    /* ── drag-and-drop for queue ── */
    const [draggedQueueId,  setDraggedQueueId]  = useState(null)
    const [dragOverQueueId, setDragOverQueueId] = useState(null)

    const pollingRef  = useRef(null)
    const pairPollRef = useRef({})

    /* ── derived helpers ── */
    const viewMode    = tabViewModes[activeTab] || 'grid'
    const setViewMode = (mode) => setTabViewModes(prev => ({ ...prev, [activeTab]: mode }))

    /* ── data loading ── */
    const loadPairs = async () => {
        try {
            const p = await getPairs()
            setPairs(p)
            p.filter(pair => pair.status === 'transcribing').forEach(pair => {
                if (!pairPollRef.current[pair.id]) startPairPolling(pair.id)
            })
        } catch (err) {
            setError(err.message)
        }
    }

    const loadQueue = async () => {
        try {
            const data = await getTranscriptionQueue()
            setQueue(data)
        } catch { /* silent */ }
    }

    const loadHistory = async () => {
        if (historyLoaded && queueHistory.length > 0) return
        setHistoryLoading(true)
        try {
            const data = await getQueueHistory(100)
            setQueueHistory(data)
            setHistoryLoaded(true)
        } catch (err) {
            setError(err.message)
        } finally {
            setHistoryLoading(false)
        }
    }

    useEffect(() => {
        (async () => {
            try {
                const [p, q] = await Promise.all([getPairs(), getTranscriptionQueue()])
                setPairs(p)
                setQueue(q)
                p.filter(pair => pair.status === 'transcribing').forEach(pair => {
                    if (!pairPollRef.current[pair.id]) startPairPolling(pair.id)
                })
            } catch (err) {
                setError(err.message)
            } finally {
                setLoading(false)
            }
        })()
        pollingRef.current = setInterval(loadQueue, 3000)
        return () => {
            if (pollingRef.current) clearInterval(pollingRef.current)
            Object.values(pairPollRef.current).forEach(clearInterval)
        }
    }, []) // eslint-disable-line react-hooks/exhaustive-deps

    useEffect(() => { if (tab) setActiveTab(tab) }, [tab])
    useEffect(() => { if (showHistory && !historyLoaded) loadHistory() }, [showHistory]) // eslint-disable-line react-hooks/exhaustive-deps
    // Reset author/series filters when switching tabs (options change per tab)
    useEffect(() => { setAuthorFilter(''); setSeriesFilter('') }, [activeTab])

    /* ── per-pair polling ── */
    const startPairPolling = (pairId) => {
        if (pairPollRef.current[pairId]) return
        pairPollRef.current[pairId] = setInterval(async () => {
            try {
                const s = await getTranscriptionStatus(pairId)
                setStatuses(prev => ({ ...prev, [pairId]: s }))
                if (s.status === 'synced' || s.status === 'error') {
                    stopPairPolling(pairId)
                    loadPairs()
                }
            } catch { /* ignore */ }
        }, 3000)
    }

    const stopPairPolling = (pairId) => {
        if (pairPollRef.current[pairId]) {
            clearInterval(pairPollRef.current[pairId])
            delete pairPollRef.current[pairId]
        }
    }

    /* ── action handlers ── */
    const handleAddToQueue = async (pairId) => {
        setError('')
        try { await addToQueue([pairId]); loadPairs(); loadQueue() }
        catch (err) { setError(err.message) }
    }

    const handleQueueSeries = async (seriesPairs) => {
        setError('')
        try { await addToQueue(seriesPairs.map(p => p.id)); loadPairs(); loadQueue() }
        catch (err) { setError(err.message) }
    }

    const handleAddVisibleToQueue = async () => {
        if (notTranscribedPairs.length === 0) return
        setError('')
        try { await addToQueue(notTranscribedPairs.map(p => p.id)); loadPairs(); loadQueue() }
        catch (err) { setError(err.message) }
    }

    const handleCancel = async (pairId) => {
        if (!confirm('Cancel this transcription?')) return
        try { await cancelTranscription(pairId); stopPairPolling(pairId); loadPairs(); loadQueue() }
        catch (err) { setError(err.message) }
    }

    const handleRemove = async (itemId) => {
        if (!confirm('Remove this item from the queue?')) return
        try { await removeFromQueue(itemId); loadQueue() }
        catch (err) { setError(err.message) }
    }

    const handleMovePriority = async (itemId, direction) => {
        const item = queue.find(q => q.id === itemId)
        if (!item) return
        const newPriority = direction === 'up' ? Math.max(1, item.priority - 10) : item.priority + 10
        try { await updateQueuePriority(itemId, newPriority); loadQueue() }
        catch (err) { setError(err.message) }
    }

    const toggleSeriesExpanded = (name) => {
        setExpandedSeries(prev => {
            const next = new Set(prev)
            next.has(name) ? next.delete(name) : next.add(name)
            return next
        })
    }

    const toggleHistorySort = (col) => {
        if (historySortCol === col) {
            setHistorySortDir(d => d === 'asc' ? 'desc' : 'asc')
        } else {
            setHistorySortCol(col)
            setHistorySortDir('asc')
        }
    }

    /* ── drag-and-drop ── */
    const handleQueueDragStart = (id) => setDraggedQueueId(id)
    const handleQueueDragOver  = (e, id) => { e.preventDefault(); setDragOverQueueId(id) }
    const handleQueueDragEnd   = () => { setDraggedQueueId(null); setDragOverQueueId(null) }

    const handleQueueDrop = async (targetId) => {
        if (!draggedQueueId || draggedQueueId === targetId) { handleQueueDragEnd(); return }
        const pending = pendingQueueItems // captured from render scope via ref-stable approach
        const draggedIdx = pending.findIndex(q => q.id === draggedQueueId)
        const targetIdx  = pending.findIndex(q => q.id === targetId)
        if (draggedIdx === -1 || targetIdx === -1) { handleQueueDragEnd(); return }

        // Place dragged item just before the target
        let newPriority
        if (targetIdx === 0) {
            newPriority = Math.max(1, pending[0].priority - 10)
        } else {
            // Find the item just before targetIdx, ignoring the dragged item
            const withoutDragged = pending.filter(q => q.id !== draggedQueueId)
            const adjTargetIdx = withoutDragged.findIndex(q => q.id === targetId)
            const before = withoutDragged[adjTargetIdx - 1]
            const after  = withoutDragged[adjTargetIdx]
            if (before) {
                newPriority = Math.round((before.priority + after.priority) / 2)
                if (newPriority === before.priority) newPriority = after.priority - 1
            } else {
                newPriority = Math.max(1, after.priority - 10)
            }
        }

        try { await updateQueuePriority(draggedQueueId, Math.max(1, newPriority)); loadQueue() }
        catch (err) { setError(err.message) }
        handleQueueDragEnd()
    }

    /* ── derived data ── */
    const canQueue = (pair) =>
        ['auto_matched', 'manual_matched', 'error'].includes(pair.status)

    const matchedPairs = useMemo(() =>
        pairs.filter(p => p.status !== 'unmatched'),
    [pairs])

    // Options scoped to the current tab's status (before search/author/series filters)
    const tabBasePairs = useMemo(() => {
        if (activeTab === 'not-transcribed') return matchedPairs.filter(p => ['auto_matched', 'manual_matched', 'error'].includes(p.status))
        if (activeTab === 'in-progress')     return matchedPairs.filter(p => p.status === 'transcribing')
        if (activeTab === 'transcribed')     return matchedPairs.filter(p => p.status === 'synced')
        return matchedPairs
    }, [activeTab, matchedPairs])

    const allAuthors = useMemo(() => {
        const s = new Set(tabBasePairs.map(p => p.ebook?.author || p.audiobook?.author).filter(Boolean))
        return [...s].sort()
    }, [tabBasePairs])

    const allSeriesNames = useMemo(() => {
        const s = new Set(tabBasePairs.map(p => p.ebook?.series || p.audiobook?.series).filter(Boolean))
        return [...s].sort()
    }, [tabBasePairs])

    const filteredPairs = useMemo(() => matchedPairs.filter(p => {
        if (searchQuery) {
            const q = searchQuery.toLowerCase()
            const series = (p.ebook?.series || p.audiobook?.series || '').toLowerCase()
            if (!(
                (p.ebook?.title    || '').toLowerCase().includes(q) ||
                (p.audiobook?.title || '').toLowerCase().includes(q) ||
                (p.ebook?.author   || '').toLowerCase().includes(q) ||
                series.includes(q)
            )) return false
        }
        if (authorFilter) {
            const a = p.ebook?.author || p.audiobook?.author || ''
            if (a !== authorFilter) return false
        }
        if (seriesFilter) {
            const s = p.ebook?.series || p.audiobook?.series || ''
            if (s !== seriesFilter) return false
        }
        return true
    }), [matchedPairs, searchQuery, authorFilter, seriesFilter])

    const notTranscribedPairs = useMemo(() =>
        filteredPairs.filter(p => ['auto_matched', 'manual_matched', 'error'].includes(p.status)),
    [filteredPairs])

    const inProgress = useMemo(() =>
        filteredPairs.filter(p => p.status === 'transcribing'),
    [filteredPairs])

    const transcribed = useMemo(() =>
        filteredPairs.filter(p => p.status === 'synced'),
    [filteredPairs])

    const isFiltered = !!(searchQuery || authorFilter || seriesFilter)

    // Sort helper
    const sortPairs = (arr) => [...arr].sort((a, b) => {
        const getV = p => {
            if (sortField === 'author') return (p.ebook?.author || p.audiobook?.author || '').toLowerCase()
            if (sortField === 'series') return (p.ebook?.series || p.audiobook?.series || '').toLowerCase()
            return (p.ebook?.title || p.audiobook?.title || '').toLowerCase()
        }
        const va = getV(a), vb = getV(b)
        const cmp = va < vb ? -1 : va > vb ? 1 : 0
        return sortDir === 'asc' ? cmp : -cmp
    })

    const sortedNotTranscribed = useMemo(() => sortPairs(notTranscribedPairs), [notTranscribedPairs, sortField, sortDir]) // eslint-disable-line react-hooks/exhaustive-deps
    const sortedTranscribed    = useMemo(() => sortPairs(transcribed),         [transcribed,         sortField, sortDir]) // eslint-disable-line react-hooks/exhaustive-deps
    const sortedInProgress     = useMemo(() => sortPairs(inProgress),          [inProgress,          sortField, sortDir]) // eslint-disable-line react-hooks/exhaustive-deps

    // Series grouping for Not Transcribed
    const { seriesGroups, noSeriesPairs } = useMemo(() => {
        const groups = {}
        const noSeries = []
        notTranscribedPairs.forEach(pair => {
            const series = pair.ebook?.series || pair.audiobook?.series
            const idx    = pair.ebook?.series_index ?? pair.audiobook?.series_index
            if (series) {
                if (!groups[series]) groups[series] = { name: series, pairs: [] }
                groups[series].pairs.push({ ...pair, _seriesIndex: idx })
            } else {
                noSeries.push(pair)
            }
        })
        Object.values(groups).forEach(g =>
            g.pairs.sort((a, b) => (a._seriesIndex ?? 999) - (b._seriesIndex ?? 999))
        )
        return {
            seriesGroups: Object.values(groups).sort((a, b) => a.name.localeCompare(b.name)),
            noSeriesPairs: noSeries,
        }
    }, [notTranscribedPairs])

    // Queue derived
    const activeQueueItem   = queue.find(q => q.status === 'in_progress')
    const pendingQueueItems = queue.filter(q => q.status === 'pending')

    // Pair-by-id map for queue covers
    const pairById = useMemo(() => {
        const m = {}
        pairs.forEach(p => { m[p.id] = p })
        return m
    }, [pairs])

    const sortedHistory = useMemo(() => {
        return [...queueHistory].sort((a, b) => {
            const av = a[historySortCol] ? new Date(a[historySortCol]) : null
            const bv = b[historySortCol] ? new Date(b[historySortCol]) : null
            if (!av && !bv) return 0
            if (!av) return 1
            if (!bv) return -1
            return historySortDir === 'asc' ? av - bv : bv - av
        })
    }, [queueHistory, historySortCol, historySortDir])

    /* ── tabs config (order: Not Transcribed → Queue → In Progress → Transcribed) ── */
    const tabs = [
        { id: 'not-transcribed', label: 'Not Transcribed', count: notTranscribedPairs.length },
        { id: 'queue',           label: 'Queue',           count: queue.length },
        { id: 'in-progress',     label: 'In Progress',     count: inProgress.length },
        { id: 'transcribed',     label: 'Transcribed',     count: transcribed.length },
    ]

    if (loading) {
        return <div className="loading-page"><div className="spinner"></div> Loading...</div>
    }

    /* ── render helpers ── */
    const renderStatusBadge = (pair) => {
        const loc = statuses[pair.id]
        const st  = loc?.status || pair.status
        if (st === 'error')  return <span className="transcription-status-badge error">Error</span>
        if (st === 'synced') return <span className="transcription-status-badge synced">Synced</span>
        return <span className="transcription-status-badge ready">Ready</span>
    }

    /* ── View toggle widget ── */
    const renderViewToggle = (modes) => (
        <div className="library-view-toggle">
            {modes.map(({ mode, icon }) => (
                <button
                    key={mode}
                    className={`library-view-btn${viewMode === mode ? ' active' : ''}`}
                    onClick={() => setViewMode(mode)}
                    title={mode === 'grid' ? 'Grid view' : mode === 'series' ? 'Series view' : 'List view'}
                >
                    {icon}
                </button>
            ))}
        </div>
    )

    /* ── Not Transcribed — Series Card ── */
    const renderSeriesCard = (name, seriesPairs) => {
        const isOpen    = expandedSeries.has(name)
        const queueable = seriesPairs.filter(p => canQueue(p))
        return (
            <div key={name} className="transcription-series-card">
                <div className="transcription-series-header" onClick={() => toggleSeriesExpanded(name)}>
                    <ChevronIcon open={isOpen} />
                    <span className="transcription-series-title">{name}</span>
                    <span className="transcription-series-count">{seriesPairs.length}</span>
                    {queueable.length > 0 && (
                        <button
                            className="btn btn-primary btn-sm"
                            onClick={(e) => { e.stopPropagation(); handleQueueSeries(queueable) }}
                        >
                            Queue All {queueable.length}
                        </button>
                    )}
                </div>
                {isOpen && seriesPairs.map(pair => (
                    <div key={pair.id} className="transcription-book-row">
                        {pair.ebook?.cover_path
                            ? <img src={coverSrc(pair.ebook.cover_path)} className="transcription-book-thumb" alt="" />
                            : <div className="transcription-book-thumb">📚</div>
                        }
                        <div className="transcription-book-index">
                            {pair._seriesIndex != null ? `#${pair._seriesIndex}` : '—'}
                        </div>
                        <div className="transcription-book-info">
                            <div className="transcription-book-title">
                                <Link to={`/book/ebook/${pair.ebook?.id}`} style={{ color: 'inherit', textDecoration: 'none' }}>
                                    {pair.ebook?.title || `Pair #${pair.id}`}
                                </Link>
                            </div>
                            <div className="transcription-book-formats">
                                {pair.ebook?.format} · {pair.audiobook?.format}
                            </div>
                        </div>
                        {renderStatusBadge(pair)}
                        {canQueue(pair) && (
                            <button
                                className="btn btn-primary btn-sm"
                                style={{ flexShrink: 0 }}
                                onClick={() => handleAddToQueue(pair.id)}
                            >
                                Queue
                            </button>
                        )}
                    </div>
                ))}
            </div>
        )
    }

    /* ── Not Transcribed — Flat Grid Card ── */
    const renderFlatCard = (pair) => (
        <div key={pair.id} className="transcription-card">
            <div className="transcription-card-cover">
                {pair.ebook?.cover_path
                    ? <img src={coverSrc(pair.ebook.cover_path)} alt="" />
                    : <div className="transcription-card-placeholder">📚</div>
                }
                {renderStatusBadge(pair)}
                {canQueue(pair) && (
                    <div className="transcription-card-action">
                        <button
                            className="btn btn-primary btn-sm"
                            onClick={(e) => { e.stopPropagation(); handleAddToQueue(pair.id) }}
                        >
                            + Queue
                        </button>
                    </div>
                )}
            </div>
            <div className="transcription-card-title">
                {pair.ebook?.title || pair.audiobook?.title || `Pair #${pair.id}`}
            </div>
            <div className="transcription-card-author">
                {pair.ebook?.author || pair.audiobook?.author || ''}
            </div>
            {pair.ebook?.format && (
                <div className="transcription-card-meta">
                    {pair.ebook.format} · {pair.audiobook?.format || ''}
                </div>
            )}
        </div>
    )

    /* ── In Progress — Grid Card ── */
    const renderProgressGridCard = (pair) => {
        const loc      = statuses[pair.id] || {}
        const progress = loc.progress
        const isError  = loc.status === 'error'
        return (
            <div key={pair.id} className="transcription-card">
                <div className="transcription-card-cover">
                    {pair.ebook?.cover_path
                        ? <img src={coverSrc(pair.ebook.cover_path)} alt="" />
                        : <div className="transcription-card-placeholder">📚</div>
                    }
                    {progress != null && !isError && (
                        <div className="transcription-ip-overlay">
                            <div className="progress-bar" style={{ margin: '0 8px' }}>
                                <div
                                    className="progress-fill"
                                    style={{ width: `${(progress * 100).toFixed(1)}%`, transition: 'width 0.5s ease' }}
                                />
                            </div>
                            <div className="transcription-ip-pct">{(progress * 100).toFixed(0)}%</div>
                        </div>
                    )}
                    <div className="transcription-card-action">
                        <button className="btn btn-sm btn-danger" onClick={() => handleCancel(pair.id)}>
                            Cancel
                        </button>
                    </div>
                </div>
                <div className="transcription-card-title">
                    {pair.ebook?.title || `Pair #${pair.id}`}
                </div>
                <div className="transcription-card-author">{pair.ebook?.author || ''}</div>
            </div>
        )
    }

    /* ── In Progress — List Card (horizontal) ── */
    const renderProgressListCard = (pair) => {
        const loc      = statuses[pair.id] || {}
        const message  = loc.message || 'Transcribing...'
        const progress = loc.progress
        const isError  = loc.status === 'error'
        return (
            <div key={pair.id} className="transcription-progress-card">
                {pair.ebook?.cover_path
                    ? <img src={coverSrc(pair.ebook.cover_path)} className="transcription-progress-thumb" alt="" />
                    : <div className="transcription-progress-thumb">📚</div>
                }
                <div className="transcription-progress-body">
                    <div className="transcription-progress-title">
                        {pair.ebook?.title || `Pair #${pair.id}`}
                    </div>
                    <div className="transcription-progress-author">{pair.ebook?.author || ''}</div>
                    <div className={`transcription-progress-message${isError ? ' error' : ''}`}>
                        {isError ? '❌ ' : ''}{message}
                    </div>
                    {progress != null && !isError && (
                        <>
                            <div className="progress-bar">
                                <div
                                    className="progress-fill"
                                    style={{ width: `${(progress * 100).toFixed(1)}%`, transition: 'width 0.5s ease' }}
                                />
                            </div>
                            <div className="transcription-progress-pct">
                                {(progress * 100).toFixed(1)}% overall
                            </div>
                        </>
                    )}
                </div>
                <button
                    className="btn btn-sm btn-danger"
                    onClick={() => handleCancel(pair.id)}
                    style={{ flexShrink: 0, alignSelf: 'flex-start' }}
                >
                    Cancel
                </button>
            </div>
        )
    }

    /* ── Transcribed — Grid Card ── */
    const renderTranscribedGridCard = (pair) => (
        <div key={pair.id} className="transcription-card">
            <div className="transcription-card-cover">
                {pair.ebook?.cover_path
                    ? <img src={coverSrc(pair.ebook.cover_path)} alt="" />
                    : <div className="transcription-card-placeholder">📚</div>
                }
                <span className="transcription-status-badge synced">Synced</span>
                <div className="transcription-card-action">
                    <Link
                        to={`/transcription/edit/${pair.id}`}
                        className="btn btn-secondary btn-sm"
                        style={{ textDecoration: 'none', whiteSpace: 'nowrap' }}
                        onClick={e => e.stopPropagation()}
                    >
                        Edit Transcription
                    </Link>
                </div>
            </div>
            <div className="transcription-card-title">
                {pair.ebook?.title || `Pair #${pair.id}`}
            </div>
            <div className="transcription-card-author">{pair.ebook?.author || ''}</div>
            {pair.synced_at && (
                <div className="transcription-card-meta">
                    {new Date(pair.synced_at).toLocaleDateString()}
                </div>
            )}
        </div>
    )

    /* ── Transcribed — List Row ── */
    const renderTranscribedListRow = (pair) => (
        <div key={pair.id} className="transcription-book-row">
            {pair.ebook?.cover_path
                ? <img src={coverSrc(pair.ebook.cover_path)} className="transcription-book-thumb" alt="" />
                : <div className="transcription-book-thumb">📚</div>
            }
            <div className="transcription-book-info">
                <div className="transcription-book-title">
                    {pair.ebook?.title || `Pair #${pair.id}`}
                </div>
                <div className="transcription-book-formats">{pair.ebook?.author || ''}</div>
            </div>
            {pair.synced_at && (
                <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)', whiteSpace: 'nowrap', flexShrink: 0 }}>
                    {new Date(pair.synced_at).toLocaleDateString()}
                </div>
            )}
            <Link
                to={`/transcription/edit/${pair.id}`}
                className="btn btn-secondary btn-sm"
                style={{ textDecoration: 'none', flexShrink: 0 }}
            >
                Edit Transcription
            </Link>
        </div>
    )

    /* ── Queue — Grid Card (draggable) ── */
    const renderQueueGridCard = (item, idx) => {
        const coverPath = pairById[item.book_pair_id]?.ebook?.cover_path
        return (
            <div
                key={item.id}
                className={`transcription-card transcription-queue-card${dragOverQueueId === item.id ? ' drag-over' : ''}`}
                draggable={canManageQueue}
                onDragStart={() => handleQueueDragStart(item.id)}
                onDragOver={e => handleQueueDragOver(e, item.id)}
                onDrop={() => handleQueueDrop(item.id)}
                onDragEnd={handleQueueDragEnd}
            >
                <div className="transcription-card-cover">
                    {coverPath
                        ? <img src={coverSrc(coverPath)} alt="" />
                        : <div className="transcription-card-placeholder">📚</div>
                    }
                    <span className="transcription-queue-badge">{idx + 1}</span>
                    {canManageQueue && (
                        <div className="transcription-card-action">
                            <button
                                className="btn btn-sm btn-danger"
                                onClick={e => { e.stopPropagation(); handleRemove(item.id) }}
                            >
                                Remove
                            </button>
                        </div>
                    )}
                </div>
                <div className="transcription-card-title">
                    {item.book_title || `Pair #${item.book_pair_id}`}
                </div>
            </div>
        )
    }

    /* ── History sort indicator ── */
    const SortIndicator = ({ col }) => {
        if (historySortCol !== col) return <span style={{ opacity: 0.3, marginLeft: 4 }}>⇅</span>
        return <span style={{ marginLeft: 4 }}>{historySortDir === 'asc' ? '↑' : '↓'}</span>
    }

    /* ── History status badge ── */
    const historyBadge = (status) => {
        const labels = {
            completed:   '✅ Completed',
            failed:      '❌ Failed',
            cancelled:   '⚪ Cancelled',
            in_progress: '🔄 In Progress',
            pending:     '⏳ Pending',
        }
        return <span className={`transcription-history-badge ${status}`}>{labels[status] || status}</span>
    }

    /* ── Queue visible/all button ── */
    const queueBtnLabel = isFiltered
        ? `Queue Visible ${notTranscribedPairs.length}`
        : `Queue All ${notTranscribedPairs.length}`

    /* ── Mobile search filtering ── */
    const mobileQ = mobileSearch.toLowerCase()
    const mobileFilterPair = (p) => {
        if (!mobileQ) return true
        return (
            (p.ebook?.title  || p.audiobook?.title  || '').toLowerCase().includes(mobileQ) ||
            (p.ebook?.author || p.audiobook?.author || '').toLowerCase().includes(mobileQ) ||
            (p.ebook?.series || p.audiobook?.series || '').toLowerCase().includes(mobileQ)
        )
    }
    const mobileFilterQueue = (item) => {
        if (!mobileQ) return true
        const pair = pairById[item.book_pair_id]
        return (
            (item.book_title || '').toLowerCase().includes(mobileQ) ||
            (pair?.ebook?.title  || pair?.audiobook?.title  || '').toLowerCase().includes(mobileQ) ||
            (pair?.ebook?.author || pair?.audiobook?.author || '').toLowerCase().includes(mobileQ) ||
            (pair?.ebook?.series || pair?.audiobook?.series || '').toLowerCase().includes(mobileQ)
        )
    }
    const mobileInProgress       = mobileQ ? inProgress.filter(mobileFilterPair)             : inProgress
    const mobilePendingQueue      = mobileQ ? pendingQueueItems.filter(mobileFilterQueue)     : pendingQueueItems
    const mobileNotTranscribed    = mobileQ ? sortedNotTranscribed.filter(mobileFilterPair)  : sortedNotTranscribed
    const mobileTranscribed       = mobileQ ? sortedTranscribed.filter(mobileFilterPair)     : sortedTranscribed
    const mobileActiveQueueItem   = mobileQ
        ? (activeQueueItem && mobileFilterQueue(activeQueueItem) ? activeQueueItem : null)
        : activeQueueItem

    /* ── Mobile item renderers ── */
    const MOBILE_NOT_TRANSCRIBED_LIMIT = 5
    const MOBILE_TRANSCRIBED_LIMIT = 5

    const renderMobileItem = (pair, action) => (
        <div key={pair.id} className="tx-mobile-item">
            {pair.ebook?.cover_path
                ? <img src={coverSrc(pair.ebook.cover_path)} className="tx-mobile-item-thumb" alt="" />
                : <div className="tx-mobile-item-thumb-placeholder">📚</div>
            }
            <div className="tx-mobile-item-info">
                <div className="tx-mobile-item-title">
                    <Link to={`/book/ebook/${pair.ebook?.id}`} style={{ color: 'inherit', textDecoration: 'none' }}>
                        {pair.ebook?.title || pair.audiobook?.title || `Pair #${pair.id}`}
                    </Link>
                </div>
                <div className="tx-mobile-item-author">
                    {pair.ebook?.author || pair.audiobook?.author || ''}
                </div>
            </div>
            <div className="tx-mobile-item-action">{action}</div>
        </div>
    )

    const renderMobileActiveCard = (pair) => {
        const loc = statuses[pair.id] || {}
        const progress = loc.progress
        const message = loc.message || 'Transcribing...'
        return (
            <div key={pair.id} className="tx-mobile-active-card">
                {pair.ebook?.cover_path
                    ? <img src={coverSrc(pair.ebook.cover_path)} className="tx-mobile-active-thumb" alt="" />
                    : <div className="tx-mobile-item-thumb-placeholder" style={{ width: 80, height: 112 }}>📚</div>
                }
                <div className="tx-mobile-active-body">
                    <div className="tx-mobile-active-title">
                        {pair.ebook?.title || `Pair #${pair.id}`}
                    </div>
                    <div className="tx-mobile-active-author">
                        {pair.ebook?.author || ''}
                    </div>
                    {progress != null && (
                        <div className="tx-mobile-active-progress">
                            <div className="progress-bar">
                                <div className="progress-fill" style={{ width: `${(progress * 100).toFixed(1)}%` }} />
                            </div>
                            <div className="tx-mobile-active-pct">
                                {(progress * 100).toFixed(0)}% Synced
                            </div>
                        </div>
                    )}
                    {!progress && (
                        <div className="tx-mobile-active-time" style={{ color: 'var(--warning)' }}>
                            {message}
                        </div>
                    )}
                </div>
                <div className="tx-mobile-active-glow" />
            </div>
        )
    }

    const renderMobileQueueItem = (item, idx) => {
        const pair = pairById[item.book_pair_id]
        return (
            <div key={item.id} className="tx-mobile-item">
                {pair?.ebook?.cover_path
                    ? <img src={coverSrc(pair.ebook.cover_path)} className="tx-mobile-item-thumb" alt="" />
                    : <div className="tx-mobile-item-thumb-placeholder">📚</div>
                }
                <div className="tx-mobile-item-info">
                    <div className="tx-mobile-item-title">
                        {item.book_title || `Pair #${item.book_pair_id}`}
                    </div>
                    <div className="tx-mobile-item-author">
                        #{idx + 1} in queue
                    </div>
                </div>
                {canManageQueue && (
                    <div className="tx-mobile-item-action">
                        <button
                            className="tx-mobile-queue-btn remove"
                            onClick={() => handleRemove(item.id)}
                        >✕</button>
                    </div>
                )}
            </div>
        )
    }

    /* ── JSX ── */
    return (
        <div className="transcription-page">
            {/* ── Error ── */}
            {error && <div className="alert alert-error" style={{ marginBottom: '16px' }}>⚠️ {error}</div>}

            {/* ══════════════ MOBILE LAYOUT ══════════════ */}
            {isMobile && (
                <div className="tx-mobile-layout">
                    {/* ── Mobile Search ── */}
                    <div className="tx-mobile-search">
                        <span className="search-icon material-symbols-outlined" style={{ fontSize: 20 }}>search</span>
                        <input
                            type="text"
                            placeholder="Search transcriptions..."
                            value={mobileSearch}
                            onChange={e => setMobileSearch(e.target.value)}
                        />
                        {mobileSearch && (
                            <button className="tx-mobile-search-clear" onClick={() => setMobileSearch('')}>
                                <span className="material-symbols-outlined" style={{ fontSize: 18 }}>close</span>
                            </button>
                        )}
                    </div>

                    {/* ── In Progress Section ── */}
                    <div className="tx-mobile-section">
                        <div className="tx-mobile-section-header" onClick={() => toggleMobileSection('in-progress')}>
                            <div className="tx-mobile-section-header-left">
                                <span className="tx-mobile-section-dot amber" />
                                <span className="tx-mobile-section-label">In Progress ({mobileInProgress.length})</span>
                            </div>
                            <span className={`material-symbols-outlined tx-mobile-section-chevron${mobileSections['in-progress'] ? ' open' : ''}`}>
                                expand_more
                            </span>
                        </div>
                        {mobileSections['in-progress'] && (
                            <div className="tx-mobile-section-content">
                                {mobileInProgress.length === 0 && !mobileActiveQueueItem ? (
                                    <div className="tx-mobile-empty-hint">{mobileQ ? 'No matches' : 'No active transcriptions'}</div>
                                ) : (
                                    <>
                                        {/* Active queue item card (processing) */}
                                        {mobileActiveQueueItem && pairById[mobileActiveQueueItem.book_pair_id] && (
                                            (() => {
                                                const pair = pairById[mobileActiveQueueItem.book_pair_id]
                                                const progress = mobileActiveQueueItem.progress
                                                const message = mobileActiveQueueItem.message || 'Processing...'
                                                return (
                                                    <div className="tx-mobile-active-card">
                                                        {pair.ebook?.cover_path
                                                            ? <img src={coverSrc(pair.ebook.cover_path)} className="tx-mobile-active-thumb" alt="" />
                                                            : <div className="tx-mobile-item-thumb-placeholder" style={{ width: 80, height: 112 }}>📚</div>
                                                        }
                                                        <div className="tx-mobile-active-body">
                                                            <div className="tx-mobile-active-title">
                                                                {mobileActiveQueueItem.book_title || pair.ebook?.title || `Pair #${pair.id}`}
                                                            </div>
                                                            <div className="tx-mobile-active-author">
                                                                {pair.ebook?.author || ''}
                                                            </div>
                                                            {progress != null && (
                                                                <div className="tx-mobile-active-progress">
                                                                    <div className="progress-bar">
                                                                        <div className="progress-fill" style={{ width: `${(progress * 100).toFixed(1)}%` }} />
                                                                    </div>
                                                                    <div className="tx-mobile-active-pct">
                                                                        {(progress * 100).toFixed(0)}% Synced
                                                                    </div>
                                                                </div>
                                                            )}
                                                            {!progress && (
                                                                <div className="tx-mobile-active-time" style={{ color: 'var(--warning)' }}>
                                                                    {message}
                                                                </div>
                                                            )}
                                                        </div>
                                                        <div className="tx-mobile-active-glow" />
                                                    </div>
                                                )
                                            })()
                                        )}
                                        {/* In-progress pairs (from pair status) */}
                                        {mobileInProgress.map(pair => renderMobileActiveCard(pair))}
                                    </>
                                )}
                            </div>
                        )}
                    </div>

                    {/* ── Queued Section ── */}
                    <div className="tx-mobile-section">
                        <div className="tx-mobile-section-header" onClick={() => toggleMobileSection('queue')}>
                            <div className="tx-mobile-section-header-left">
                                <span className="tx-mobile-section-dot blue" />
                                <span className="tx-mobile-section-label">Queued ({mobilePendingQueue.length})</span>
                            </div>
                            <span className={`material-symbols-outlined tx-mobile-section-chevron${mobileSections['queue'] ? ' open' : ''}`}>
                                expand_more
                            </span>
                        </div>
                        {mobileSections['queue'] && (
                            <div className="tx-mobile-section-content">
                                {mobilePendingQueue.length === 0 ? (
                                    <div className="tx-mobile-empty-hint">{mobileQ ? 'No matches' : 'No items in queue'}</div>
                                ) : (
                                    mobilePendingQueue.map((item, idx) => renderMobileQueueItem(item, idx))
                                )}
                            </div>
                        )}
                    </div>

                    {/* ── Not Transcribed Section ── */}
                    <div className="tx-mobile-section">
                        <div className="tx-mobile-section-header" onClick={() => toggleMobileSection('not-transcribed')}>
                            <div className="tx-mobile-section-header-left">
                                <span className="tx-mobile-section-dot gray" />
                                <span className="tx-mobile-section-label">Not Transcribed ({mobileNotTranscribed.length})</span>
                            </div>
                            <span className={`material-symbols-outlined tx-mobile-section-chevron${mobileSections['not-transcribed'] ? ' open' : ''}`}>
                                expand_more
                            </span>
                        </div>
                        {mobileSections['not-transcribed'] && (
                            <div className="tx-mobile-section-content">
                                {mobileNotTranscribed.length === 0 ? (
                                    <div className="tx-mobile-empty-hint">{mobileQ ? 'No matches' : 'All pairs have been transcribed'}</div>
                                ) : (
                                    <>
                                        {(mobileShowAllNT || mobileQ ? mobileNotTranscribed : mobileNotTranscribed.slice(0, MOBILE_NOT_TRANSCRIBED_LIMIT)).map(pair =>
                                            renderMobileItem(pair,
                                                canQueue(pair) && (
                                                    <button
                                                        className="tx-mobile-queue-btn"
                                                        onClick={() => handleAddToQueue(pair.id)}
                                                    >Queue</button>
                                                )
                                            )
                                        )}
                                        {!mobileShowAllNT && !mobileQ && mobileNotTranscribed.length > MOBILE_NOT_TRANSCRIBED_LIMIT && (
                                            <button className="tx-mobile-show-all" onClick={() => setMobileShowAllNT(true)}>
                                                Show all {mobileNotTranscribed.length}
                                            </button>
                                        )}
                                    </>
                                )}
                            </div>
                        )}
                    </div>

                    {/* ── Transcribed Section ── */}
                    <div className="tx-mobile-section">
                        <div className="tx-mobile-section-header" onClick={() => toggleMobileSection('transcribed')}>
                            <div className="tx-mobile-section-header-left">
                                <span className="tx-mobile-section-dot green" />
                                <span className="tx-mobile-section-label">Transcribed ({mobileTranscribed.length})</span>
                            </div>
                            <span className={`material-symbols-outlined tx-mobile-section-chevron${mobileSections['transcribed'] ? ' open' : ''}`}>
                                expand_more
                            </span>
                        </div>
                        {mobileSections['transcribed'] && (
                            <div className="tx-mobile-section-content">
                                {mobileTranscribed.length === 0 ? (
                                    <div className="tx-mobile-empty-hint">{mobileQ ? 'No matches' : 'No transcribed pairs yet'}</div>
                                ) : (
                                    <>
                                        {(mobileShowAllTx || mobileQ ? mobileTranscribed : mobileTranscribed.slice(0, MOBILE_TRANSCRIBED_LIMIT)).map(pair =>
                                            renderMobileItem(pair,
                                                <Link
                                                    to={`/transcription/edit/${pair.id}`}
                                                    className="tx-mobile-view-link"
                                                    onClick={e => e.stopPropagation()}
                                                >View</Link>
                                            )
                                        )}
                                        {!mobileShowAllTx && !mobileQ && mobileTranscribed.length > MOBILE_TRANSCRIBED_LIMIT && (
                                            <button className="tx-mobile-show-all" onClick={() => setMobileShowAllTx(true)}>
                                                Show all {mobileTranscribed.length}
                                            </button>
                                        )}
                                    </>
                                )}
                            </div>
                        )}
                    </div>
                </div>
            )}

            {/* ══════════════ DESKTOP LAYOUT ══════════════ */}
            {!isMobile && (
                <>
                    {/* ── Toolbar ── */}
                    <div className="transcription-toolbar">
                        <div className="transcription-toolbar-left">
                            {/* Tab pills */}
                            <div className="library-filter-pills">
                                {tabs.map(t => (
                                    <button
                                        key={t.id}
                                        className={`library-filter-pill${activeTab === t.id ? ' active' : ''}`}
                                        onClick={() => setActiveTab(t.id)}
                                    >
                                        {t.label} ({t.count})
                                    </button>
                                ))}
                            </div>

                            {/* Author/Series filter pills */}
                            <FilterPill label="Author" value={authorFilter} options={allAuthors} onChange={setAuthorFilter} />
                            <FilterPill label="Series" value={seriesFilter} options={allSeriesNames} onChange={setSeriesFilter} />

                            {/* Active search chip */}
                            {searchQuery && (
                                <div className="library-search-active">
                                    <span>Search: <strong>{searchQuery}</strong></span>
                                    <button className="library-search-clear" onClick={() => setSearchParams({}, { replace: true })}>✕</button>
                                </div>
                            )}
                        </div>

                        <div className="transcription-toolbar-right">
                            {/* Sort pill — shown on tabs with sortable content */}
                            {(activeTab === 'not-transcribed' || activeTab === 'transcribed' || activeTab === 'in-progress') && (
                                <SortPill
                                    sortField={sortField} setSortField={setSortField}
                                    sortDir={sortDir} setSortDir={setSortDir}
                                />
                            )}

                            {/* View toggle */}
                            {activeTab === 'not-transcribed' && renderViewToggle([
                                { mode: 'grid',   icon: <GridIcon /> },
                                { mode: 'series', icon: <ListIcon /> },
                            ])}
                            {(activeTab === 'in-progress' || activeTab === 'transcribed' || activeTab === 'queue') && renderViewToggle([
                                { mode: 'grid', icon: <GridIcon /> },
                                { mode: 'list', icon: <ListIcon /> },
                            ])}

                            {/* Queue All / Queue Visible */}
                            {activeTab === 'not-transcribed' && notTranscribedPairs.length > 0 && (
                                <button className="btn btn-primary btn-sm" onClick={handleAddVisibleToQueue}>
                                    {queueBtnLabel}
                                </button>
                            )}
                        </div>
                    </div>

                    {/* ── Stats bar ── */}
                    <div className="transcription-stats">
                        <span className="transcription-stat"><strong>{notTranscribedPairs.length}</strong> not transcribed</span>
                        <span className="transcription-stat"><strong>{inProgress.length}</strong> in progress</span>
                        <span className="transcription-stat"><strong>{transcribed.length}</strong> transcribed</span>
                        {queue.length > 0 && (
                            <span className="transcription-stat"><strong>{queue.length}</strong> in queue</span>
                        )}
                    </div>

                    {/* ══════════════ NOT TRANSCRIBED ══════════════ */}
                    {activeTab === 'not-transcribed' && (
                        <>
                            {notTranscribedPairs.length === 0 ? (
                                <div className="transcription-empty">
                                    <div className="transcription-empty-icon">🎙️</div>
                                    <h3>No pairs waiting for transcription</h3>
                                    <p>
                                        {matchedPairs.length === 0
                                            ? 'Match some ebooks with audiobooks first.'
                                            : isFiltered
                                                ? 'No results match the current filters.'
                                                : 'All matched pairs have been transcribed or are in progress.'}
                                    </p>
                                </div>
                            ) : viewMode === 'series' ? (
                                <div>
                                    {seriesGroups.map(g => renderSeriesCard(g.name, g.pairs))}
                                    {noSeriesPairs.length > 0 && renderSeriesCard('No Series', noSeriesPairs)}
                                </div>
                            ) : (
                                <div className="transcription-grid">
                                    {sortedNotTranscribed.map(renderFlatCard)}
                                </div>
                            )}
                        </>
                    )}

                    {/* ══════════════ QUEUE ══════════════ */}
                    {activeTab === 'queue' && (
                        <>
                            {/* Currently Processing */}
                            {activeQueueItem && (
                                <>
                                    <p className="transcription-queue-section-title">Currently Processing</p>
                                    <div className="transcription-queue-active">
                                        <div className="transcription-queue-active-header">
                                            <div>
                                                <div className="transcription-queue-active-title">
                                                    {activeQueueItem.book_title || `Pair #${activeQueueItem.book_pair_id}`}
                                                </div>
                                                <div className="transcription-queue-active-msg">
                                                    {activeQueueItem.message || 'Processing...'}
                                                    {activeQueueItem.retry_count > 0 && (
                                                        <span style={{ marginLeft: 10, color: 'var(--warning)' }}>
                                                            🔁 Retry #{activeQueueItem.retry_count}
                                                        </span>
                                                    )}
                                                </div>
                                            </div>
                                            <button
                                                className="btn btn-sm btn-danger"
                                                onClick={() => handleCancel(activeQueueItem.book_pair_id)}
                                            >
                                                Cancel
                                            </button>
                                        </div>
                                        {activeQueueItem.progress != null && (
                                            <>
                                                <div className="progress-bar">
                                                    <div
                                                        className="progress-fill"
                                                        style={{ width: `${(activeQueueItem.progress * 100).toFixed(1)}%`, transition: 'width 0.5s ease' }}
                                                    />
                                                </div>
                                                <div className="transcription-progress-pct">
                                                    {(activeQueueItem.progress * 100).toFixed(1)}% complete
                                                </div>
                                            </>
                                        )}
                                        {activeQueueItem.started_at && (
                                            <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: 8 }}>
                                                Started: {formatDate(activeQueueItem.started_at)}
                                            </div>
                                        )}
                                    </div>
                                </>
                            )}

                            {/* Pending */}
                            <p className="transcription-queue-section-title">
                                Pending ({pendingQueueItems.length})
                                {canManageQueue && viewMode === 'grid' && pendingQueueItems.length > 1 && (
                                    <span style={{ fontSize: '0.75rem', fontWeight: 400, marginLeft: 8, color: 'var(--text-muted)' }}>
                                        drag to reorder
                                    </span>
                                )}
                            </p>

                            {pendingQueueItems.length === 0 && !activeQueueItem ? (
                                <div className="transcription-empty">
                                    <div className="transcription-empty-icon">📋</div>
                                    <h3>Queue is empty</h3>
                                    <p>Add books from the Not Transcribed tab to start processing.</p>
                                </div>
                            ) : pendingQueueItems.length === 0 ? (
                                <div style={{ color: 'var(--text-secondary)', fontSize: '0.88rem', padding: '12px 0' }}>
                                    No more items waiting.
                                </div>
                            ) : viewMode === 'grid' ? (
                                <div className="transcription-grid">
                                    {pendingQueueItems.map((item, idx) => renderQueueGridCard(item, idx))}
                                </div>
                            ) : (
                                <>
                                    {pendingQueueItems.map((item, idx) => (
                                        <div key={item.id} className="transcription-queue-item">
                                            <div className="transcription-queue-number">{idx + 1}</div>
                                            <div style={{ flex: 1, minWidth: 0 }}>
                                                <div className="transcription-queue-item-title">
                                                    {item.book_title || `Pair #${item.book_pair_id}`}
                                                </div>
                                                <div className="transcription-queue-item-meta">
                                                    Priority: {item.priority} · Added: {formatDate(item.created_at)}
                                                    {item.retry_count > 0 && (
                                                        <span style={{ marginLeft: 8, color: 'var(--warning)' }}>
                                                            🔁 Retry #{item.retry_count}
                                                        </span>
                                                    )}
                                                </div>
                                            </div>
                                            {canManageQueue && (
                                                <div className="transcription-queue-actions">
                                                    <button className="btn btn-secondary btn-sm" onClick={() => handleMovePriority(item.id, 'up')} disabled={idx === 0} title="Move up">↑</button>
                                                    <button className="btn btn-secondary btn-sm" onClick={() => handleMovePriority(item.id, 'down')} disabled={idx === pendingQueueItems.length - 1} title="Move down">↓</button>
                                                    <button className="btn btn-danger btn-sm" onClick={() => handleRemove(item.id)} title="Remove">✕</button>
                                                </div>
                                            )}
                                        </div>
                                    ))}
                                </>
                            )}

                            {/* History toggle */}
                            <div
                                className="transcription-history-toggle"
                                onClick={() => setShowHistory(h => !h)}
                            >
                                <svg
                                    className={`transcription-history-chevron${showHistory ? ' open' : ''}`}
                                    viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="14" height="14"
                                >
                                    <polyline points="9 18 15 12 9 6" />
                                </svg>
                                History {queueHistory.length > 0 ? `(${queueHistory.length})` : ''}
                                {historyLoading && (
                                    <span style={{ fontSize: '0.78rem', marginLeft: 8, color: 'var(--text-muted)' }}>Loading...</span>
                                )}
                            </div>

                            {showHistory && !historyLoading && queueHistory.length === 0 && (
                                <div className="transcription-empty" style={{ padding: '32px 0' }}>
                                    <div className="transcription-empty-icon">📜</div>
                                    <h3>No history yet</h3>
                                    <p>Completed, failed, and cancelled jobs will appear here.</p>
                                </div>
                            )}

                            {showHistory && sortedHistory.length > 0 && (
                                <div style={{ overflowX: 'auto' }}>
                                    <table className="transcription-history-table">
                                        <thead>
                                            <tr>
                                                <th>Book</th>
                                                <th>Status</th>
                                                <th>Retries</th>
                                                <th className="sortable" onClick={() => toggleHistorySort('created_at')}>Created<SortIndicator col="created_at" /></th>
                                                <th className="sortable" onClick={() => toggleHistorySort('started_at')}>Started<SortIndicator col="started_at" /></th>
                                                <th className="sortable" onClick={() => toggleHistorySort('completed_at')}>Completed<SortIndicator col="completed_at" /></th>
                                                <th>Duration</th>
                                                <th>Message</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {sortedHistory.map(item => (
                                                <tr key={item.id}>
                                                    <td>
                                                        <div style={{ maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontWeight: 600 }}>
                                                            {item.book_title || `Pair #${item.book_pair_id}`}
                                                        </div>
                                                    </td>
                                                    <td>{historyBadge(item.status)}</td>
                                                    <td style={{ textAlign: 'center' }}>
                                                        {item.retry_count > 0
                                                            ? <span style={{ color: 'var(--warning)', fontWeight: 600 }}>{item.retry_count}</span>
                                                            : <span style={{ color: 'var(--text-muted)' }}>0</span>
                                                        }
                                                    </td>
                                                    <td style={{ fontSize: '0.78rem', color: 'var(--text-secondary)' }}>{formatDate(item.created_at)}</td>
                                                    <td style={{ fontSize: '0.78rem', color: 'var(--text-secondary)' }}>{formatDate(item.started_at)}</td>
                                                    <td style={{ fontSize: '0.78rem', color: 'var(--text-secondary)' }}>{formatDate(item.completed_at)}</td>
                                                    <td style={{ fontSize: '0.78rem', fontWeight: 500 }}>{formatDuration(item.started_at, item.completed_at)}</td>
                                                    <td>
                                                        <div
                                                            style={{ maxWidth: 250, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: '0.78rem', color: item.status === 'failed' ? 'var(--error)' : 'var(--text-secondary)' }}
                                                            title={item.error_message || item.message || ''}
                                                        >
                                                            {item.error_message || item.message || '—'}
                                                        </div>
                                                    </td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            )}
                        </>
                    )}

                    {/* ══════════════ IN PROGRESS ══════════════ */}
                    {activeTab === 'in-progress' && (
                        <>
                            {inProgress.length === 0 ? (
                                <div className="transcription-empty">
                                    <div className="transcription-empty-icon">⏳</div>
                                    <h3>No transcriptions in progress</h3>
                                    <p>Add books to the queue from the Not Transcribed tab.</p>
                                </div>
                            ) : viewMode === 'grid' ? (
                                <div className="transcription-grid">
                                    {sortedInProgress.map(renderProgressGridCard)}
                                </div>
                            ) : (
                                <div className="transcription-progress-list">
                                    {sortedInProgress.map(renderProgressListCard)}
                                </div>
                            )}
                        </>
                    )}

                    {/* ══════════════ TRANSCRIBED ══════════════ */}
                    {activeTab === 'transcribed' && (
                        <>
                            {transcribed.length === 0 ? (
                                <div className="transcription-empty">
                                    <div className="transcription-empty-icon">✅</div>
                                    <h3>No transcribed pairs yet</h3>
                                    <p>{isFiltered ? 'No results match the current filters.' : 'Once transcription completes, pairs will appear here.'}</p>
                                </div>
                            ) : viewMode === 'grid' ? (
                                <div className="transcription-grid">
                                    {sortedTranscribed.map(renderTranscribedGridCard)}
                                </div>
                            ) : (
                                <div className="transcription-series-card" style={{ overflow: 'hidden' }}>
                                    {sortedTranscribed.map(renderTranscribedListRow)}
                                </div>
                            )}
                        </>
                    )}
                </>
            )}
        </div>
    )
}

export default TranscriptionPage
