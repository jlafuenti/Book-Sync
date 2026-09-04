import { useEffect, useState } from 'react'
import Modal from './Modal'

/**
 * The one bulk metadata editor (issue #276).
 *
 * LibraryPage and SeriesPage each carried a copy: the same five-field state,
 * the same five `if (field.trim())` patch lines, the same markup and the same
 * "Save to N Items" label. Only what happened *after* the save differed
 * (`browse.patchItems` vs a full `loadAll()`), which is why this component
 * hands the caller a patch and nothing else.
 *
 * The two copies differed in two cosmetic ways, both kept as props so no page's
 * visible text changed: the description line, and whether the busy label shows
 * a spinner.
 */

const FIELDS = [
    { key: 'author', label: 'Author', type: 'text' },
    { key: 'series', label: 'Series', type: 'text' },
    { key: 'series_index', label: 'Series Index', type: 'number' },
    { key: 'publisher', label: 'Publisher', type: 'text' },
    { key: 'published_year', label: 'Published Year', type: 'number' },
]

const BLANK = { author: '', series: '', series_index: '', publisher: '', published_year: '' }

// Blank means "leave this alone", so an empty field is absent from the patch
// rather than present-and-empty. A number that will not parse becomes null,
// which is how both pages have always cleared a value.
export function buildPatch(fields) {
    const patch = {}
    if (fields.author.trim()) patch.author = fields.author.trim()
    if (fields.series.trim()) patch.series = fields.series.trim()
    if (fields.series_index.trim()) patch.series_index = parseFloat(fields.series_index) || null
    if (fields.publisher.trim()) patch.publisher = fields.publisher.trim()
    if (fields.published_year.trim()) patch.published_year = parseInt(fields.published_year) || null
    return patch
}

export default function BulkMetadataEditModal({
    open,
    selectionCount,
    saving = false,
    description,
    savingLabel = 'Saving...',
    onSave,
    onClose,
}) {
    const [fields, setFields] = useState(BLANK)

    // A reopen starts clean — both pages used to reset their copy of this
    // state by hand after every successful save.
    useEffect(() => { if (open) setFields(BLANK) }, [open])

    const plural = selectionCount !== 1 ? 's' : ''
    const nothingToSave = Object.values(fields).every(v => !v.trim())

    const handleSave = () => {
        const patch = buildPatch(fields)
        if (Object.keys(patch).length === 0) return
        onSave(patch)
    }

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
            style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: '12px', padding: '24px', maxWidth: '520px', width: '90%', boxShadow: '0 8px 32px rgba(0,0,0,0.6)' }}
        >
            <h3 id="bulk-metadata-edit-title" style={{ marginTop: 0 }}>Edit {selectionCount} Item{plural}</h3>
            {description && (
                <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem', marginTop: 0 }}>{description}</p>
            )}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                {FIELDS.map(({ key, label, type }) => (
                    <div key={key}>
                        <label style={{ display: 'block', marginBottom: '4px', fontSize: '0.9rem', fontWeight: 500 }}>{label}</label>
                        <input
                            className="form-input"
                            type={type}
                            placeholder="Leave blank to keep unchanged"
                            value={fields[key]}
                            onChange={e => setFields(prev => ({ ...prev, [key]: e.target.value }))}
                        />
                    </div>
                ))}
            </div>
            <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end', marginTop: '20px' }}>
                <button className="btn btn-secondary" onClick={onClose} disabled={saving}>Cancel</button>
                <button
                    className="btn btn-primary"
                    onClick={handleSave}
                    disabled={saving || nothingToSave}
                >
                    {saving ? savingLabel : `Save to ${selectionCount} Item${plural}`}
                </button>
            </div>
        </Modal>
    )
}
