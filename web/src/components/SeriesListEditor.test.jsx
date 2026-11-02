import { describe, it, expect, vi } from 'vitest'
import { useState } from 'react'
import { render, screen, fireEvent, within } from '@testing-library/react'
import SeriesListEditor from './SeriesListEditor'

// Issue #907: a book is in a list of series, entry 0 being the primary. The
// editor is controlled: it hands every change to `onChange` as a new list.

function Harness({ initial = [], suggestions, onChange = () => {} }) {
    const [value, setValue] = useState(initial)
    return (
        <SeriesListEditor
            value={value}
            suggestions={suggestions}
            onChange={(next) => { setValue(next); onChange(next) }}
        />
    )
}

const TWO = [{ name: 'Axis Saga', index: 4 }, { name: 'Axis Chronological', index: 2.5 }]

const names = () => screen.queryAllByLabelText(/^Series name \d+$/).map(i => i.value)
const numbers = () => screen.queryAllByLabelText(/^Series number \d+$/).map(i => i.value)

describe('SeriesListEditor (issue #907)', () => {
    it('shows one row per series with its name and number', () => {
        render(<Harness initial={TWO} />)
        expect(names()).toEqual(['Axis Saga', 'Axis Chronological'])
        expect(numbers()).toEqual(['4', '2.5'])
    })

    it('marks only the first row as the primary series', () => {
        render(<Harness initial={TWO} />)
        expect(screen.getAllByText('Primary')).toHaveLength(1)
        const firstRow = screen.getByLabelText('Series name 1').closest('.series-editor-row')
        expect(within(firstRow).getByText('Primary')).toBeInTheDocument()
    })

    it('says so when there are no series, and still offers Add series', () => {
        render(<Harness />)
        expect(screen.getByText('No series')).toBeInTheDocument()
        expect(screen.getByRole('button', { name: 'Add series' })).toBeInTheDocument()
    })

    it('Add series appends a blank row', () => {
        const onChange = vi.fn()
        render(<Harness initial={[TWO[0]]} onChange={onChange} />)

        fireEvent.click(screen.getByRole('button', { name: 'Add series' }))

        expect(names()).toEqual(['Axis Saga', ''])
        expect(onChange).toHaveBeenLastCalledWith([TWO[0], { name: '', index: null }])
    })

    it('editing the name and the number changes just that row', () => {
        const onChange = vi.fn()
        render(<Harness initial={TWO} onChange={onChange} />)

        fireEvent.change(screen.getByLabelText('Series name 2'), { target: { value: 'Axis Timeline' } })
        fireEvent.change(screen.getByLabelText('Series number 2'), { target: { value: '7' } })

        expect(onChange).toHaveBeenLastCalledWith([TWO[0], { name: 'Axis Timeline', index: '7' }])
    })

    it('an emptied number becomes null', () => {
        const onChange = vi.fn()
        render(<Harness initial={TWO} onChange={onChange} />)

        fireEvent.change(screen.getByLabelText('Series number 1'), { target: { value: '' } })

        expect(onChange).toHaveBeenLastCalledWith([{ name: 'Axis Saga', index: null }, TWO[1]])
    })

    it('Remove drops that row, and removing the first promotes the next to primary', () => {
        render(<Harness initial={TWO} />)

        fireEvent.click(screen.getByRole('button', { name: 'Remove series 1' }))

        expect(names()).toEqual(['Axis Chronological'])
        const row = screen.getByLabelText('Series name 1').closest('.series-editor-row')
        expect(within(row).getByText('Primary')).toBeInTheDocument()
    })

    it('Move down and Move up reorder the rows, changing which is primary', () => {
        render(<Harness initial={TWO} />)

        fireEvent.click(screen.getByRole('button', { name: 'Move series 1 down' }))
        expect(names()).toEqual(['Axis Chronological', 'Axis Saga'])
        expect(numbers()).toEqual(['2.5', '4'])

        fireEvent.click(screen.getByRole('button', { name: 'Move series 2 up' }))
        expect(names()).toEqual(['Axis Saga', 'Axis Chronological'])
    })

    it('cannot move the first row up or the last row down', () => {
        render(<Harness initial={TWO} />)
        expect(screen.getByRole('button', { name: 'Move series 1 up' })).toBeDisabled()
        expect(screen.getByRole('button', { name: 'Move series 2 down' })).toBeDisabled()
        expect(screen.getByRole('button', { name: 'Move series 1 down' })).toBeEnabled()
    })

    it('suggests the library\'s series as you type in a name input', () => {
        const { container } = render(<Harness suggestions={['The Quill Files', 'Axis Saga', 'Other Series']} initial={[{ name: '', index: null }]} />)

        const input = screen.getByLabelText('Series name 1')
        fireEvent.focus(input)
        fireEvent.change(input, { target: { value: 'quil' } })

        expect(screen.getAllByRole('option').map(o => o.textContent)).toEqual(['The Quill Files'])
        expect(container.querySelector('datalist')).toBeNull()
        expect(input).not.toHaveAttribute('list')
    })

    it('picking a suggestion fills that row with the exact name', () => {
        const onChange = vi.fn()
        render(<Harness suggestions={['The Quill Files']} initial={[{ name: '', index: '3' }]} onChange={onChange} />)

        const input = screen.getByLabelText('Series name 1')
        fireEvent.focus(input)
        fireEvent.change(input, { target: { value: 'quil' } })
        fireEvent.click(screen.getByRole('option', { name: 'The Quill Files' }))

        expect(onChange).toHaveBeenLastCalledWith([{ name: 'The Quill Files', index: '3' }])
    })

    it('does not suggest a name another row already uses', () => {
        render(<Harness suggestions={['Axis Saga', 'Axis Chronological']} initial={[TWO[0], { name: '', index: null }]} />)

        const second = screen.getByLabelText('Series name 2')
        fireEvent.focus(second)
        fireEvent.change(second, { target: { value: 'axis' } })

        expect(screen.getAllByRole('option').map(o => o.textContent)).toEqual(['Axis Chronological'])
    })

    it('Enter on a highlighted suggestion does not submit the surrounding form', () => {
        const onSubmit = vi.fn(e => e.preventDefault())
        render(<form onSubmit={onSubmit}><Harness suggestions={['Axis Saga']} initial={[{ name: '', index: null }]} /></form>)

        const input = screen.getByLabelText('Series name 1')
        fireEvent.focus(input)
        fireEvent.change(input, { target: { value: 'axis' } })
        fireEvent.keyDown(input, { key: 'ArrowDown' })
        fireEvent.keyDown(input, { key: 'Enter' })

        expect(input).toHaveValue('Axis Saga')
        expect(onSubmit).not.toHaveBeenCalled()
    })

    it('works without suggestions', () => {
        render(<Harness initial={TWO} />)
        expect(screen.getByLabelText('Series name 1')).toBeInTheDocument()
    })

    it('the buttons do not submit a surrounding form', () => {
        const onSubmit = vi.fn(e => e.preventDefault())
        render(<form onSubmit={onSubmit}><Harness initial={TWO} /></form>)

        fireEvent.click(screen.getByRole('button', { name: 'Add series' }))
        fireEvent.click(screen.getByRole('button', { name: 'Remove series 1' }))
        fireEvent.click(screen.getByRole('button', { name: 'Move series 1 down' }))

        expect(onSubmit).not.toHaveBeenCalled()
    })
})
