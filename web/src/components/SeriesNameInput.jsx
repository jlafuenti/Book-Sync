import { useId, useMemo, useState } from 'react'

/**
 * A text input for a series name that suggests the series already in the
 * library as you type (issue #907), like a tag picker.
 *
 * Controlled: `value` / `onChange(name)`. `suggestions` are the known names;
 * `exclude` are names to leave out (the other rows of a series list already
 * use them). While focused it lists up to MAX_OPTIONS suggestions containing
 * the typed text, case-insensitively and alphabetically (an empty input lists
 * the first few). The suggestion is never forced: text that matches nothing
 * stays exactly as typed.
 *
 * Mouse picks use onMouseDown + preventDefault so the input does not blur
 * (which closes the list) before the click lands. Keys: ArrowDown / ArrowUp
 * move the highlight and wrap, Enter picks it (and is swallowed so a
 * surrounding form does not submit), Escape closes, Tab and blur close.
 * Plain JSX, no HTML sinks. Other props (aria-label, className, placeholder,
 * ...) go to the input.
 */
const MAX_OPTIONS = 8

const fold = (s) => String(s ?? '').trim().toLowerCase()

export default function SeriesNameInput({ value, onChange, suggestions = [], exclude = [], ...inputProps }) {
    const listId = useId()
    const [open, setOpen] = useState(false)
    const [active, setActive] = useState(-1)

    const matches = useMemo(() => {
        const needle = fold(value)
        const skip = new Set(exclude.map(fold))
        skip.add(needle)
        return [...new Set(suggestions)]
            .filter(name => !skip.has(fold(name)) && fold(name).includes(needle))
            .sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }))
            .slice(0, MAX_OPTIONS)
    }, [value, suggestions, exclude])

    const showList = open && matches.length > 0
    const activeIndex = showList && active < matches.length ? active : -1
    const optionId = (i) => `${listId}-option-${i}`

    const pick = (name) => {
        onChange(name)
        setOpen(false)
        setActive(-1)
    }

    const handleKeyDown = (e) => {
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault()
            if (!showList) {
                setOpen(true)
                return
            }
            const step = e.key === 'ArrowDown' ? 1 : -1
            const from = activeIndex === -1 ? (step === 1 ? -1 : 0) : activeIndex
            setActive((from + step + matches.length) % matches.length)
        } else if (e.key === 'Enter') {
            if (activeIndex !== -1) {
                e.preventDefault()
                pick(matches[activeIndex])
            }
        } else if (e.key === 'Escape') {
            if (showList) e.stopPropagation()
            setOpen(false)
        } else if (e.key === 'Tab') {
            setOpen(false)
        }
    }

    return (
        <div className="series-typeahead">
            <input
                {...inputProps}
                type="text"
                role="combobox"
                aria-expanded={showList}
                aria-autocomplete="list"
                aria-controls={listId}
                aria-activedescendant={activeIndex === -1 ? undefined : optionId(activeIndex)}
                autoComplete="off"
                value={value}
                onChange={e => { onChange(e.target.value); setOpen(true); setActive(-1) }}
                onFocus={() => setOpen(true)}
                onBlur={() => setOpen(false)}
                onKeyDown={handleKeyDown}
            />
            {showList && (
                <ul className="series-typeahead-list" id={listId} role="listbox">
                    {matches.map((name, i) => (
                        <li
                            key={name}
                            id={optionId(i)}
                            role="option"
                            aria-selected={i === activeIndex}
                            className={`series-typeahead-option${i === activeIndex ? ' active' : ''}`}
                            onMouseDown={e => e.preventDefault()}
                            onClick={() => pick(name)}
                        >
                            {name}
                        </li>
                    ))}
                </ul>
            )}
        </div>
    )
}
