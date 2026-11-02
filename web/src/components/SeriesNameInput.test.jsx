import { describe, it, expect, vi } from 'vitest'
import { useState } from 'react'
import { render, screen, fireEvent } from '@testing-library/react'
import SeriesNameInput from './SeriesNameInput'

// Issue #907: a type-ahead for series names. Typing shows the library's
// existing series that contain the text; picking one fills the exact name.

const SUGGESTIONS = ['The Quill Files', 'Axis Saga', 'Axis Chronological', 'Bartleby Cycle']

function Harness({ initial = '', suggestions = SUGGESTIONS, exclude, onChange = () => {}, onSubmit }) {
    const [value, setValue] = useState(initial)
    const input = (
        <SeriesNameInput
            value={value}
            suggestions={suggestions}
            exclude={exclude}
            aria-label="Series name 1"
            className="form-input"
            placeholder="Series"
            onChange={(v) => { setValue(v); onChange(v) }}
        />
    )
    return onSubmit ? <form onSubmit={onSubmit}>{input}</form> : input
}

const input = () => screen.getByRole('combobox')
const options = () => screen.queryAllByRole('option').map(o => o.textContent)
const focus = () => fireEvent.focus(input())
const type = (text) => fireEvent.change(input(), { target: { value: text } })
const key = (k) => fireEvent.keyDown(input(), { key: k })

describe('SeriesNameInput (issue #907)', () => {
    it('is a text input that passes through its label, class and placeholder', () => {
        render(<Harness initial="Axis" />)
        expect(input()).toHaveValue('Axis')
        expect(input()).toHaveAccessibleName('Series name 1')
        expect(input()).toHaveClass('form-input')
        expect(input()).toHaveAttribute('placeholder', 'Series')
    })

    it('shows nothing until it is focused', () => {
        render(<Harness />)
        expect(screen.queryByRole('listbox')).toBeNull()
    })

    it('an empty input lists the suggestions alphabetically, at most 8', () => {
        const many = Array.from({ length: 12 }, (_, i) => `Series ${String.fromCharCode(76 - i)}`)
        render(<Harness suggestions={many} />)
        focus()
        expect(options()).toEqual(['Series A', 'Series B', 'Series C', 'Series D', 'Series E', 'Series F', 'Series G', 'Series H'])
    })

    it('typing narrows the list to names containing the text, in any case', () => {
        render(<Harness />)
        focus()
        type('quil')
        expect(options()).toEqual(['The Quill Files'])

        type('AXIS')
        expect(options()).toEqual(['Axis Chronological', 'Axis Saga'])
    })

    it('matches inside a name, not only at the start', () => {
        render(<Harness />)
        focus()
        type('files')
        expect(options()).toEqual(['The Quill Files'])
    })

    it('shows no dropdown when nothing matches, and keeps what was typed', () => {
        const onChange = vi.fn()
        render(<Harness onChange={onChange} />)
        focus()
        type('Brand New Series')
        expect(screen.queryByRole('listbox')).toBeNull()
        expect(input()).toHaveValue('Brand New Series')
        expect(onChange).toHaveBeenLastCalledWith('Brand New Series')
    })

    it('leaves out excluded names and the exact current value', () => {
        render(<Harness initial="Axis Saga" exclude={['axis chronological']} />)
        focus()
        // "Axis Saga" is the value, "Axis Chronological" is used by another row
        expect(options()).toEqual([])

        type('axis')
        expect(options()).toEqual(['Axis Saga'])
    })

    it('limits a long match list to 8', () => {
        const many = Array.from({ length: 20 }, (_, i) => `Axis ${String(i).padStart(2, '0')}`)
        render(<Harness suggestions={many} />)
        focus()
        type('axis')
        expect(options()).toHaveLength(8)
    })

    it('clicking an option sets the name and closes the list', () => {
        const onChange = vi.fn()
        render(<Harness onChange={onChange} />)
        focus()
        type('quil')

        const option = screen.getByRole('option', { name: 'The Quill Files' })
        // mousedown must not blur the input, or the list would close before the click lands
        const notPrevented = fireEvent.mouseDown(option)
        expect(notPrevented).toBe(false)
        fireEvent.click(option)

        expect(onChange).toHaveBeenLastCalledWith('The Quill Files')
        expect(input()).toHaveValue('The Quill Files')
        expect(screen.queryByRole('listbox')).toBeNull()
    })

    it('ArrowDown and ArrowUp move the highlight and wrap around', () => {
        render(<Harness />)
        focus()
        type('axis')   // Axis Chronological, Axis Saga
        const selected = () => screen.queryAllByRole('option').filter(o => o.getAttribute('aria-selected') === 'true').map(o => o.textContent)
        expect(selected()).toEqual([])

        key('ArrowDown'); expect(selected()).toEqual(['Axis Chronological'])
        key('ArrowDown'); expect(selected()).toEqual(['Axis Saga'])
        key('ArrowDown'); expect(selected()).toEqual(['Axis Chronological'])
        key('ArrowUp');   expect(selected()).toEqual(['Axis Saga'])
    })

    it('ArrowUp from nothing highlighted goes to the last option', () => {
        render(<Harness />)
        focus()
        type('axis')
        key('ArrowUp')
        expect(screen.getByRole('option', { name: 'Axis Saga' })).toHaveAttribute('aria-selected', 'true')
    })

    it('Enter picks the highlighted option and does not submit the form', () => {
        const onSubmit = vi.fn(e => e.preventDefault())
        const onChange = vi.fn()
        render(<Harness onSubmit={onSubmit} onChange={onChange} />)
        focus()
        type('axis')
        key('ArrowDown')
        key('ArrowDown')

        const notPrevented = fireEvent.keyDown(input(), { key: 'Enter' })

        expect(notPrevented).toBe(false)
        expect(onChange).toHaveBeenLastCalledWith('Axis Saga')
        expect(input()).toHaveValue('Axis Saga')
        expect(screen.queryByRole('listbox')).toBeNull()
        expect(onSubmit).not.toHaveBeenCalled()
    })

    it('Enter with nothing highlighted is left alone, so the form still submits', () => {
        render(<Harness />)
        focus()
        type('axis')
        const notPrevented = fireEvent.keyDown(input(), { key: 'Enter' })
        expect(notPrevented).toBe(true)
        expect(input()).toHaveValue('axis')
    })

    it('Escape closes the list, and typing opens it again', () => {
        render(<Harness />)
        focus()
        type('axis')
        expect(screen.getByRole('listbox')).toBeInTheDocument()

        key('Escape')
        expect(screen.queryByRole('listbox')).toBeNull()
        expect(input()).toHaveValue('axis')

        type('axis s')
        expect(options()).toEqual(['Axis Saga'])
    })

    it('Tab and blur close the list without changing the value', () => {
        render(<Harness />)
        focus()
        type('axis')
        key('Tab')
        expect(screen.queryByRole('listbox')).toBeNull()

        type('axis s')
        expect(screen.getByRole('listbox')).toBeInTheDocument()
        fireEvent.blur(input())
        expect(screen.queryByRole('listbox')).toBeNull()
        expect(input()).toHaveValue('axis s')
    })

    it('Escape that closes the list is not passed on, so a surrounding modal stays open', () => {
        const onDocKey = vi.fn()
        document.addEventListener('keydown', onDocKey)
        try {
            render(<Harness />)
            focus()
            type('axis')
            key('Escape')
            expect(onDocKey).not.toHaveBeenCalled()

            // nothing left to close: Escape goes on to the modal
            key('Escape')
            expect(onDocKey).toHaveBeenCalledTimes(1)
        } finally {
            document.removeEventListener('keydown', onDocKey)
        }
    })

    it('ArrowDown opens a closed list', () => {
        render(<Harness />)
        focus()
        key('Escape')
        expect(screen.queryByRole('listbox')).toBeNull()
        key('ArrowDown')
        expect(screen.getByRole('listbox')).toBeInTheDocument()
    })

    it('exposes the combobox pattern to assistive technology', () => {
        render(<Harness />)
        expect(input()).toHaveAttribute('aria-autocomplete', 'list')
        expect(input()).toHaveAttribute('aria-expanded', 'false')

        focus()
        type('axis')
        expect(input()).toHaveAttribute('aria-expanded', 'true')
        const list = screen.getByRole('listbox')
        expect(input()).toHaveAttribute('aria-controls', list.id)

        key('ArrowDown')
        expect(input()).toHaveAttribute('aria-activedescendant', screen.getByRole('option', { name: 'Axis Chronological' }).id)
    })

    it('works with no suggestions at all', () => {
        render(<SeriesNameInput value="" onChange={() => {}} aria-label="Series" />)
        fireEvent.focus(screen.getByRole('combobox'))
        expect(screen.queryByRole('listbox')).toBeNull()
    })
})
