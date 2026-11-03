import { useEffect, useMemo, useState } from 'react'
import Modal from './Modal'
import SeriesNameInput from './SeriesNameInput'
import useKnownSeries from '../lib/useKnownSeries'
import { primarySeries, seriesIndexIn } from '../lib/series'

/**
 * The one bulk metadata editor (issue #276).
 *
 * LibraryPage and SeriesPage each carried a copy: the same five-field state,
 * the same five `if (field.trim())` patch lines, the same markup and the same
 * "Save to N Items" label. Only what happened *after* the save differed
 * (`browse.patchItems` vs a full `loadAll()`), which is why this component
 * hands the caller patches and nothing else.
 *
 * Issue #914: the caller passes the selected `items` (`key`, `title`, `author`,
 * `series_list`, `publisher`, `published_year`) and each field opens with the
 * value every item shares, blank (placeholder "Varies") where they differ.
 *
 * Issue #913: once a series name is entered the single index box becomes one
 * number per book, and `onSave` receives `[{ key, patch }]`, one per item in
 * the items' order, instead of one patch for all.
 */

const VARIES = 'Varies — leave blank to keep each'
const KEEP = 'Leave blank to keep unchanged'

const SERIES_MODES = [
    { value: 'replace', label: 'Replace all series' },
    { value: 'add', label: 'Add or update this series' },
]

const fold = (s) => String(s ?? '').trim().toLowerCase()

/** One value every item has, or null. `norm` returns null for an empty value. */
function common(items, norm, same = (a, b) => a === b) {
    if (!items.length) return null
    const first = norm(items[0])
    if (first === null) return null
    return items.every(it => {
        const v = norm(it)
        return v !== null && same(v, first)
    }) ? first : null
}

const textOf = (v) => {
    const s = String(v ?? '').trim()
    return s || null
}

const yearOf = (v) => {
    if (v === null || v === undefined || String(v).trim() === '') return null
    const n = Number(v)
    return Number.isFinite(n) ? n : null
}

/**
 * The values every selected item agrees on: `{ author, publisher,
 * published_year, series }`, each the shared value or null when the items
 * differ or any is empty. Series is the primary series' name (case-insensitive,
 * the first item's spelling).
 */
export function sharedValues(items) {
    return {
        author: common(items, it => textOf(it.author)),
        publisher: common(items, it => textOf(it.publisher)),
        published_year: common(items, it => yearOf(it.published_year)),
        series: common(items, it => textOf(primarySeries(it.series_list)?.name), (a, b) => fold(a) === fold(b)),
    }
}

/** The row values for a series name: each item's number in it, as text. */
function seedIndexes(items, name) {
    const out = {}
    if (!name.trim()) return out
    for (const it of items) {
        const idx = seriesIndexIn(it.series_list, name)
        out[it.key] = idx === null || idx === undefined ? '' : String(idx)
    }
    return out
}

function initialFields(items) {
    const shared = sharedValues(items)
    const series = shared.series ?? ''
    return {
        author: shared.author ?? '',
        series,
        series_mode: shared.series ? 'add' : 'replace',
        publisher: shared.publisher ?? '',
        published_year: shared.published_year === null ? '' : String(shared.published_year),
        indexes: seedIndexes(items, series),
    }
}

const parseIndex = (text) => (String(text ?? '').trim() ? parseFloat(text) || null : null)

// Blank means "leave this alone", so an empty field is absent from the patch
// rather than present-and-empty; so is a prefilled field left as it opened
// (`shared`), so saving does not rewrite every file for nothing. A number that
// will not parse becomes null, which is how both pages have always cleared a
// value.
//
// Series (issues #907, #913): a book can be in several, so the name and that
// book's number become one entry and the mode says what to do with it:
// "replace" sends the whole list (`series_list`), "add" adds or updates the
// entry on whatever each book already has (`series_add`). In add mode an entry
// is left out for a book when the name was prefilled and its number in that
// series is as it was; replace is an explicit choice and is always sent.
// Returns one `{ key, patch }` per item, in the items' order.
export function buildPatches(fields, items, shared) {
    const shape = {}
    const author = fields.author.trim()
    if (author && author !== shared.author) shape.author = author
    const publisher = fields.publisher.trim()
    if (publisher && publisher !== shared.publisher) shape.publisher = publisher
    const yearText = fields.published_year.trim()
    if (yearText) {
        const year = parseInt(yearText) || null
        if (year === null || year !== shared.published_year) shape.published_year = year
    }
    const name = fields.series.trim()
    const prefilled = Boolean(name) && shared.series !== null && fold(name) === fold(shared.series)
    return items.map(item => {
        const patch = { ...shape }
        if (name) {
            const index = parseIndex(fields.indexes?.[item.key])
            const current = seriesIndexIn(item.series_list, name)
            const unchanged = prefilled && fields.series_mode === 'add' && (current === null ? null : Number(current)) === index
            if (!unchanged) patch[fields.series_mode === 'add' ? 'series_add' : 'series_list'] = [{ name, index }]
        }
        return { key: item.key, patch }
    })
}

export default function BulkMetadataEditModal({
    open,
    items = [],
    saving = false,
    description,
    savingLabel = 'Saving...',
    onSave,
    onClose,
}) {
    const [fields, setFields] = useState(() => initialFields(items))
    // Series already in the library, suggested as the series name is typed.
    const knownSeries = useKnownSeries(open)

    // What the books shared when the modal opened is what a field is compared
    // against to decide whether the user changed it.
    const shared = useMemo(() => sharedValues(items), [items])

    // A reopen starts from the shared values — both pages used to reset their
    // copy of this state by hand after every successful save.
    useEffect(() => { if (open) setFields(initialFields(items)) }, [open])

    const count = items.length
    const plural = count !== 1 ? 's' : ''
    const patches = buildPatches(fields, items, shared)
    const nothingToSave = patches.every(p => Object.keys(p.patch).length === 0)
    const seriesName = fields.series.trim()

    const set = (key, value) => setFields(prev => ({ ...prev, [key]: value }))
    const setName = (name) => setFields(prev => ({ ...prev, series: name, indexes: seedIndexes(items, name) }))
    const setIndex = (key, value) => setFields(prev => ({ ...prev, indexes: { ...prev.indexes, [key]: value } }))
    const numberInOrder = () => setFields(prev => ({
        ...prev, indexes: Object.fromEntries(items.map((it, i) => [it.key, String(i + 1)])),
    }))

    const handleSave = () => {
        if (nothingToSave) return
        onSave(patches)
    }

    const placeholderFor = (key) => (shared[key] === null ? VARIES : KEEP)
    const labelStyle = { display: 'block', marginBottom: '4px', fontSize: '0.9rem', fontWeight: 500 }

    return (
        <Modal
            open={open}
            onClose={onClose}
            labelledBy="bulk-metadata-edit-title"
            closeOnBackdrop={false}
            closeOnEscape={!saving}
            overlayClassName=""
            overlayStyle={{
                position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
                background: 'rgba(0,0,0,0.6)', display: 'flex',
                alignItems: 'center', justifyContent: 'center', zIndex: 1000
            }}
            style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: '12px', padding: '24px', maxWidth: '520px', width: '90%', maxHeight: '90vh', overflowY: 'auto', boxShadow: '0 8px 32px rgba(0,0,0,0.6)' }}
        >
            <h3 id="bulk-metadata-edit-title" style={{ marginTop: 0 }}>Edit {count} Item{plural}</h3>
            {description && (
                <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem', marginTop: 0 }}>{description}</p>
            )}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                <div>
                    <label htmlFor="bulk-author" style={labelStyle}>Author</label>
                    <input
                        id="bulk-author"
                        className="form-input"
                        type="text"
                        placeholder={placeholderFor('author')}
                        value={fields.author}
                        onChange={e => set('author', e.target.value)}
                    />
                </div>
                <div>
                    <label htmlFor="bulk-series" style={labelStyle}>Series</label>
                    <SeriesNameInput
                        id="bulk-series"
                        className="form-input"
                        placeholder={placeholderFor('series')}
                        value={fields.series}
                        suggestions={knownSeries}
                        onChange={setName}
                    />
                    <div role="radiogroup" aria-label="Series mode" style={{ display: 'flex', gap: '16px', marginTop: '8px', fontSize: '0.9rem' }}>
                        {SERIES_MODES.map(({ value, label }) => (
                            <label key={value} style={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}>
                                <input
                                    type="radio"
                                    name="bulk-series-mode"
                                    value={value}
                                    checked={fields.series_mode === value}
                                    onChange={() => set('series_mode', value)}
                                />
                                {label}
                            </label>
                        ))}
                    </div>
                </div>
                {seriesName && (
                    <div>
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px', marginBottom: '6px' }}>
                            <span style={{ fontSize: '0.9rem', fontWeight: 500 }}>Number in {seriesName}</span>
                            <button type="button" className="btn btn-secondary btn-sm" onClick={numberInOrder}>
                                {`Number 1 to ${count} in this order`}
                            </button>
                        </div>
                        <div
                            data-testid="bulk-series-rows"
                            style={{ maxHeight: '260px', overflowY: 'auto', border: '1px solid var(--border)', borderRadius: '8px' }}
                        >
                            {items.map((item, i) => (
                                <div
                                    key={item.key}
                                    data-testid="bulk-series-row"
                                    style={{
                                        display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px',
                                        padding: '6px 10px', borderTop: i === 0 ? 'none' : '1px solid var(--border)',
                                    }}
                                >
                                    <div style={{ minWidth: 0, flex: 1 }}>
                                        <div style={{ fontSize: '0.9rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.title}</div>
                                        {item.author && (
                                            <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.author}</div>
                                        )}
                                    </div>
                                    <input
                                        className="form-input"
                                        type="number"
                                        step="0.1"
                                        aria-label={`Number for ${item.title}`}
                                        style={{ width: '80px', flex: '0 0 auto' }}
                                        value={fields.indexes[item.key] ?? ''}
                                        onChange={e => setIndex(item.key, e.target.value)}
                                    />
                                </div>
                            ))}
                        </div>
                    </div>
                )}
                <div>
                    <label htmlFor="bulk-publisher" style={labelStyle}>Publisher</label>
                    <input
                        id="bulk-publisher"
                        className="form-input"
                        type="text"
                        placeholder={placeholderFor('publisher')}
                        value={fields.publisher}
                        onChange={e => set('publisher', e.target.value)}
                    />
                </div>
                <div>
                    <label htmlFor="bulk-published-year" style={labelStyle}>Published Year</label>
                    <input
                        id="bulk-published-year"
                        className="form-input"
                        type="number"
                        placeholder={placeholderFor('published_year')}
                        value={fields.published_year}
                        onChange={e => set('published_year', e.target.value)}
                    />
                </div>
            </div>
            <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end', marginTop: '20px' }}>
                <button className="btn btn-secondary" onClick={onClose} disabled={saving}>Cancel</button>
                <button
                    className="btn btn-primary"
                    onClick={handleSave}
                    disabled={saving || nothingToSave}
                >
                    {saving ? savingLabel : `Save to ${count} Item${plural}`}
                </button>
            </div>
        </Modal>
    )
}
