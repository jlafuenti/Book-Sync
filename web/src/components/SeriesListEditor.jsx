import SeriesNameInput from './SeriesNameInput'

/**
 * The series a book belongs to, as an editable list (issue #907).
 *
 * `value` is `[{name, index}]`; entry 0 is the primary series, the one cards
 * show. Controlled: every change goes to `onChange` as a whole new list. The
 * number is kept as typed (a string, or null when empty) so a half-typed
 * "2." survives a render; `cleanSeriesList` turns it into a number on save.
 *
 * `suggestions` are the series names already in the library, offered as a
 * type-ahead (SeriesNameInput) on every name input, minus the names the other
 * rows already use. No HTML sinks: the rows are plain JSX.
 */
export default function SeriesListEditor({ value = [], onChange, suggestions = [] }) {

    const update = (i, patch) => onChange(value.map((e, j) => (j === i ? { ...e, ...patch } : e)))
    const remove = (i) => onChange(value.filter((_, j) => j !== i))
    const move = (i, by) => {
        const next = [...value]
        const [entry] = next.splice(i, 1)
        next.splice(i + by, 0, entry)
        onChange(next)
    }
    const add = () => onChange([...value, { name: '', index: null }])

    return (
        <div className="series-editor">
            {value.length === 0 && <div className="series-editor-empty">No series</div>}
            {value.map((entry, i) => (
                <div className="series-editor-row" key={i}>
                    <span className={`series-editor-primary${i === 0 ? '' : ' placeholder'}`}>
                        {i === 0 ? 'Primary' : ''}
                    </span>
                    <div className="series-editor-name">
                        <SeriesNameInput
                            className="form-input"
                            aria-label={`Series name ${i + 1}`}
                            value={entry.name}
                            suggestions={suggestions}
                            exclude={value.filter((_, j) => j !== i).map(e => e.name)}
                            onChange={name => update(i, { name })}
                        />
                    </div>
                    <input
                        className="form-input series-editor-number"
                        type="number"
                        step="0.1"
                        aria-label={`Series number ${i + 1}`}
                        value={entry.index ?? ''}
                        onChange={e => update(i, { index: e.target.value === '' ? null : e.target.value })}
                    />
                    <button
                        type="button" className="btn btn-secondary btn-sm series-editor-btn"
                        aria-label={`Move series ${i + 1} up`} title="Move up"
                        disabled={i === 0} onClick={() => move(i, -1)}
                    >&uarr;</button>
                    <button
                        type="button" className="btn btn-secondary btn-sm series-editor-btn"
                        aria-label={`Move series ${i + 1} down`} title="Move down"
                        disabled={i === value.length - 1} onClick={() => move(i, 1)}
                    >&darr;</button>
                    <button
                        type="button" className="btn btn-secondary btn-sm series-editor-btn"
                        aria-label={`Remove series ${i + 1}`} title="Remove"
                        onClick={() => remove(i)}
                    >&times;</button>
                </div>
            ))}
            <button type="button" className="btn btn-secondary btn-sm series-editor-add" onClick={add}>
                Add series
            </button>
        </div>
    )
}
