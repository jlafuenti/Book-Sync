import { describe, it, expect, vi } from 'vitest'
import { useState } from 'react'
import { render, screen, fireEvent, within } from '@testing-library/react'
import BulkMetadataEditModal, { buildPatches, sharedValues, variesAcross } from './BulkMetadataEditModal'

const { knownSeriesMock } = vi.hoisted(() => ({ knownSeriesMock: vi.fn(() => []) }))
vi.mock('../lib/useKnownSeries', () => ({ default: (...args) => knownSeriesMock(...args) }))

// Issue #276: LibraryPage and SeriesPage each carried their own copy of this
// modal. Issues #913/#914: the modal now takes the selected items, prefills
// what they share, and numbers each book in a series on its own.

function makeItems(n, extra = {}) {
    return Array.from({ length: n }, (_, i) => ({
        key: `k${i + 1}`, title: `Book ${i + 1}`, author: `Author ${i + 1}`,
        series_list: [], publisher: `Publisher ${i + 1}`, published_year: 2000 + i, ...extra,
    }))
}

function field(label) {
    return screen.getByLabelText(label)
}

function renderModal(props = {}) {
    const onSave = props.onSave ?? vi.fn()
    const items = props.items ?? makeItems(2)
    render(<BulkMetadataEditModal open items={items} onSave={onSave} onClose={vi.fn()} {...props} />)
    return onSave
}

describe('sharedValues (issue #914)', () => {
    it('returns the common value when every item has it', () => {
        const items = [
            { key: 'a', author: ' Ann Lee ', publisher: 'P', published_year: 1999, series_list: [{ name: 'Axis Saga', index: 1 }] },
            { key: 'b', author: 'Ann Lee', publisher: 'P', published_year: '1999', series_list: [{ name: 'axis saga', index: 2 }] },
        ]
        expect(sharedValues(items)).toEqual({ author: 'Ann Lee', publisher: 'P', published_year: 1999, series: 'Axis Saga' })
    })

    it('is null for a field where items differ or any is empty', () => {
        const items = [
            { key: 'a', author: 'Ann', publisher: 'P', published_year: 1999, series_list: [{ name: 'Axis Saga', index: 1 }] },
            { key: 'b', author: 'Bob', publisher: '', published_year: null, series_list: [] },
        ]
        expect(sharedValues(items)).toEqual({ author: null, publisher: null, published_year: null, series: null })
    })

    it('uses the primary series only', () => {
        const items = [
            { key: 'a', series_list: [{ name: 'Axis Saga', index: 1 }, { name: 'Other', index: 2 }] },
            { key: 'b', series_list: [{ name: 'Other', index: 1 }, { name: 'Axis Saga', index: 2 }] },
        ]
        expect(sharedValues(items).series).toBeNull()
    })

    it('is all null for no items', () => {
        expect(sharedValues([])).toEqual({ author: null, publisher: null, published_year: null, series: null })
    })
})

describe('variesAcross (issue #914)', () => {
    it('is true only where there is a non-empty value and the items do not all agree', () => {
        const items = [
            { key: 'a', author: 'Ann', publisher: '', published_year: 1999, series_list: [{ name: 'Axis Saga' }] },
            { key: 'b', author: 'Bob', publisher: ' ', published_year: 1999, series_list: [] },
        ]
        expect(variesAcross(items)).toEqual({ author: true, publisher: false, published_year: false, series: true })
    })

    it('is false when every item agrees, and for no items', () => {
        const items = [
            { key: 'a', author: 'Ann', series_list: [{ name: 'Axis Saga' }] },
            { key: 'b', author: 'Ann', series_list: [{ name: 'axis saga' }] },
        ]
        expect(variesAcross(items).author).toBe(false)
        expect(variesAcross(items).series).toBe(false)
        expect(variesAcross([])).toEqual({ author: false, publisher: false, published_year: false, series: false })
    })
})

describe('buildPatches', () => {
    const items = [
        { key: 'a', title: 'A', series_list: [{ name: 'Axis Saga', index: 1 }] },
        { key: 'b', title: 'B', series_list: [] },
    ]
    const none = { author: null, publisher: null, published_year: null, series: null }
    const blank = { author: '', series: '', series_mode: 'replace', publisher: '', published_year: '', indexes: {} }

    it('returns one empty patch per item for blank fields', () => {
        expect(buildPatches(blank, items, none)).toEqual([{ key: 'a', patch: {} }, { key: 'b', patch: {} }])
    })

    it('trims text, parses the year as an int and gives each item its own number', () => {
        const fields = {
            author: '  Robin Hobb ', series: ' Liveship Traders ', series_mode: 'replace',
            publisher: ' Bantam ', published_year: '1999', indexes: { a: '1', b: '2.5' },
        }
        expect(buildPatches(fields, items, none)).toEqual([
            { key: 'a', patch: { author: 'Robin Hobb', series_list: [{ name: 'Liveship Traders', index: 1 }], publisher: 'Bantam', published_year: 1999 } },
            { key: 'b', patch: { author: 'Robin Hobb', series_list: [{ name: 'Liveship Traders', index: 2.5 }], publisher: 'Bantam', published_year: 1999 } },
        ])
    })

    it('sends series_add in add mode, with a null number for a blank row', () => {
        const fields = { ...blank, series: 'Axis Chronological', series_mode: 'add', indexes: { a: '3', b: '' } }
        expect(buildPatches(fields, items, none)).toEqual([
            { key: 'a', patch: { series_add: [{ name: 'Axis Chronological', index: 3 }] } },
            { key: 'b', patch: { series_add: [{ name: 'Axis Chronological', index: null }] } },
        ])
    })

    it('omits a prefilled field left as it was, and a blank one', () => {
        const shared = { author: 'Ann', publisher: 'P', published_year: 1999, series: null }
        const fields = { ...blank, author: ' Ann ', publisher: '', published_year: '1999' }
        expect(buildPatches(fields, items, shared).map(p => p.patch)).toEqual([{}, {}])
        expect(buildPatches({ ...fields, published_year: '2001' }, items, shared)[0].patch).toEqual({ published_year: 2001 })
    })

    it('add mode omits the entry for an item whose number in a prefilled series is unchanged', () => {
        const shared = { ...none, series: 'Axis Saga' }
        const fields = { ...blank, series: 'Axis Saga', series_mode: 'add', indexes: { a: '1', b: '' } }
        expect(buildPatches(fields, items, shared).map(p => p.patch)).toEqual([{}, {}])
        const changed = { ...fields, indexes: { a: '4', b: '' } }
        expect(buildPatches(changed, items, shared).map(p => p.patch)).toEqual([
            { series_add: [{ name: 'Axis Saga', index: 4 }] }, {},
        ])
    })

    it('replace mode always sends the series for a non-empty name', () => {
        const shared = { ...none, series: 'Axis Saga' }
        const fields = { ...blank, series: 'Axis Saga', series_mode: 'replace', indexes: { a: '1', b: '' } }
        expect(buildPatches(fields, items, shared).map(p => p.patch)).toEqual([
            { series_list: [{ name: 'Axis Saga', index: 1 }] },
            { series_list: [{ name: 'Axis Saga', index: null }] },
        ])
    })

    it('sends null rather than NaN for a number that will not parse', () => {
        expect(buildPatches({ ...blank, published_year: 'nineteen' }, items, none)[0].patch).toEqual({ published_year: null })
    })
})

describe('BulkMetadataEditModal (issue #276)', () => {
    it('renders nothing when closed', () => {
        render(<BulkMetadataEditModal open={false} items={makeItems(3)} onSave={vi.fn()} onClose={vi.fn()} />)
        expect(screen.queryByRole('dialog')).toBeNull()
    })

    it('is a labelled dialog whose heading counts the selection', () => {
        renderModal({ items: makeItems(3) })
        const dialog = screen.getByRole('dialog')
        expect(dialog).toHaveAttribute('aria-modal', 'true')
        expect(dialog).toHaveAccessibleName('Edit 3 Items')
        expect(screen.getByRole('button', { name: 'Save to 3 Items' })).toBeInTheDocument()
    })

    it('says "Item" in the singular', () => {
        renderModal({ items: makeItems(1) })
        expect(screen.getByRole('dialog')).toHaveAccessibleName('Edit 1 Item')
        expect(screen.getByRole('button', { name: 'Save to 1 Item' })).toBeInTheDocument()
    })

    it('shows the caller`s description text', () => {
        renderModal({ description: 'Leave fields blank to keep existing values.' })
        expect(screen.getByText('Leave fields blank to keep existing values.')).toBeInTheDocument()
    })

    it('disables Save while nothing has changed, and while saving', () => {
        const blankItems = makeItems(2, { author: '', publisher: '', published_year: null })
        const { rerender } = render(
            <BulkMetadataEditModal open items={blankItems} onSave={vi.fn()} onClose={vi.fn()} />,
        )
        expect(screen.getByRole('button', { name: 'Save to 2 Items' })).toBeDisabled()

        fireEvent.change(field('Author'), { target: { value: '   ' } })
        expect(screen.getByRole('button', { name: 'Save to 2 Items' })).toBeDisabled()

        fireEvent.change(field('Author'), { target: { value: 'Robin Hobb' } })
        expect(screen.getByRole('button', { name: 'Save to 2 Items' })).toBeEnabled()

        rerender(<BulkMetadataEditModal open items={blankItems} saving onSave={vi.fn()} onClose={vi.fn()} />)
        expect(screen.getByRole('button', { name: 'Saving...' })).toBeDisabled()
        expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
    })

    it('renders the caller`s own saving label when it wants one', () => {
        renderModal({ saving: true, savingLabel: <><div className="spinner"></div> Saving...</> })
        expect(screen.getByRole('button', { name: 'Saving...' }).querySelector('.spinner')).not.toBeNull()
    })

    it('sends one patch per item: trimmed shared fields, a year as an int', () => {
        const onSave = renderModal({ items: makeItems(2, { author: '', publisher: '', published_year: null }) })

        fireEvent.change(field('Author'), { target: { value: '  Robin Hobb  ' } })
        fireEvent.change(field('Publisher'), { target: { value: ' Bantam ' } })
        fireEvent.change(field('Published Year'), { target: { value: '1999' } })
        fireEvent.click(screen.getByRole('button', { name: 'Save to 2 Items' }))

        const patch = { author: 'Robin Hobb', publisher: 'Bantam', published_year: 1999 }
        expect(onSave).toHaveBeenCalledWith([{ key: 'k1', patch }, { key: 'k2', patch }])
    })

    it('omits every field left blank', () => {
        const onSave = renderModal({ items: makeItems(1, { author: '', publisher: '', published_year: null }) })

        fireEvent.change(field('Series'), { target: { value: 'Liveship' } })
        fireEvent.click(screen.getByRole('button', { name: 'Save to 1 Item' }))

        expect(onSave).toHaveBeenCalledWith([{ key: 'k1', patch: { series_list: [{ name: 'Liveship', index: null }] } }])
    })

    // Issue #907: the series name suggests the library's existing series.
    describe('series name type-ahead (issue #907)', () => {
        it('suggests known series as you type and fills the exact name on pick', () => {
            knownSeriesMock.mockReturnValue(['The Quill Files', 'Axis Saga'])
            const onSave = renderModal()

            fireEvent.focus(field('Series'))
            fireEvent.change(field('Series'), { target: { value: 'quil' } })
            expect(screen.getAllByRole('option').map(o => o.textContent)).toEqual(['The Quill Files'])
            fireEvent.click(screen.getByRole('option', { name: 'The Quill Files' }))
            fireEvent.click(screen.getByRole('button', { name: 'Save to 2 Items' }))

            const patches = onSave.mock.calls[0][0]
            expect(patches.map(p => p.patch.series_list)).toEqual([
                [{ name: 'The Quill Files', index: null }], [{ name: 'The Quill Files', index: null }],
            ])
        })

        it('works without suggestions', () => {
            knownSeriesMock.mockReturnValue([])
            renderModal()
            fireEvent.focus(field('Series'))
            expect(screen.queryByRole('listbox')).toBeNull()
        })

        it('only asks for the known series while it is open', () => {
            knownSeriesMock.mockClear()
            const { rerender } = render(<BulkMetadataEditModal open={false} items={makeItems(2)} onSave={vi.fn()} onClose={vi.fn()} />)
            expect(knownSeriesMock).toHaveBeenLastCalledWith(false)
            rerender(<BulkMetadataEditModal open items={makeItems(2)} onSave={vi.fn()} onClose={vi.fn()} />)
            expect(knownSeriesMock).toHaveBeenLastCalledWith(true)
        })
    })

    // Issue #907: a book can be in several series, so the series field can
    // either replace the list or add to it.
    describe('series mode (issues #907, #913)', () => {
        it('defaults to replacing the series list', () => {
            renderModal()
            expect(screen.getByRole('radio', { name: 'Replace all series' })).toBeChecked()
            expect(screen.getByRole('radio', { name: 'Add or update this series' })).not.toBeChecked()
        })

        it('"Add or update this series" sends series_add, leaving the existing series alone', () => {
            const onSave = renderModal()

            fireEvent.click(screen.getByRole('radio', { name: 'Add or update this series' }))
            fireEvent.change(field('Series'), { target: { value: ' Axis Chronological ' } })
            fireEvent.change(screen.getByLabelText('Number for Book 1'), { target: { value: '3' } })
            fireEvent.click(screen.getByRole('button', { name: 'Save to 2 Items' }))

            const calls = onSave.mock.calls[0][0]
            expect(calls.map(c => c.patch.series_add)).toEqual([
                [{ name: 'Axis Chronological', index: 3 }],
                [{ name: 'Axis Chronological', index: null }],
            ])
        })

        it('has no number inputs without a series name', () => {
            renderModal({ items: makeItems(2, { author: '', publisher: '', published_year: null }) })
            expect(screen.queryByLabelText('Number for Book 1')).toBeNull()
            expect(screen.queryByText(/^Number in /)).toBeNull()
            expect(screen.getByRole('button', { name: 'Save to 2 Items' })).toBeDisabled()
        })

        it('goes back to replacing the next time it opens', () => {
            function Harness() {
                const [open, setOpen] = useState(true)
                return (
                    <div>
                        <button onClick={() => setOpen(true)}>Reopen</button>
                        <BulkMetadataEditModal open={open} items={makeItems(1)} onSave={vi.fn()} onClose={() => setOpen(false)} />
                    </div>
                )
            }
            render(<Harness />)

            fireEvent.click(screen.getByRole('radio', { name: 'Add or update this series' }))
            fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
            fireEvent.click(screen.getByRole('button', { name: 'Reopen' }))

            expect(screen.getByRole('radio', { name: 'Replace all series' })).toBeChecked()
        })
    })

    // Issue #913: one number per selected book.
    describe('per-book numbers (issue #913)', () => {
        it('shows a "Number in <name>" section with one row per book once a name is typed', () => {
            renderModal({ items: makeItems(3) })
            fireEvent.change(field('Series'), { target: { value: 'Axis Saga' } })

            expect(screen.getByText('Number in Axis Saga')).toBeInTheDocument()
            for (const t of ['Book 1', 'Book 2', 'Book 3']) {
                expect(screen.getByLabelText(`Number for ${t}`)).toHaveValue(null)
            }
            expect(screen.getByRole('button', { name: 'Number 1 to 3 in this order' })).toBeInTheDocument()
        })

        it('"Number 1 to N in this order" fills the rows in sequence, which can then be adjusted', () => {
            const onSave = renderModal({ items: makeItems(3) })
            fireEvent.change(field('Series'), { target: { value: 'Axis Saga' } })
            fireEvent.click(screen.getByRole('button', { name: 'Number 1 to 3 in this order' }))

            expect(screen.getByLabelText('Number for Book 1')).toHaveValue(1)
            expect(screen.getByLabelText('Number for Book 3')).toHaveValue(3)

            fireEvent.change(screen.getByLabelText('Number for Book 2'), { target: { value: '2.5' } })
            fireEvent.click(screen.getByRole('button', { name: 'Save to 3 Items' }))

            expect(onSave.mock.calls[0][0].map(p => p.patch.series_list[0].index)).toEqual([1, 2.5, 3])
        })

        it('re-seeds the rows from each book`s number in the named series when the name changes', () => {
            const items = [
                { key: 'a', title: 'First', author: 'X', series_list: [{ name: 'Axis Saga', index: 1 }, { name: 'Other', index: 7 }] },
                { key: 'b', title: 'Second', author: 'X', series_list: [{ name: 'Other', index: 8 }] },
            ]
            renderModal({ items })
            fireEvent.change(field('Series'), { target: { value: 'Other' } })
            expect(screen.getByLabelText('Number for First')).toHaveValue(7)
            expect(screen.getByLabelText('Number for Second')).toHaveValue(8)

            fireEvent.change(field('Series'), { target: { value: 'axis saga' } })
            expect(screen.getByLabelText('Number for First')).toHaveValue(1)
            expect(screen.getByLabelText('Number for Second')).toHaveValue(null)

            fireEvent.change(field('Series'), { target: { value: '' } })
            expect(screen.queryByLabelText('Number for First')).toBeNull()
        })

        it('shows the author next to the title', () => {
            renderModal({ items: [{ key: 'a', title: 'Book A', author: 'Ann Lee', series_list: [] }] })
            fireEvent.change(field('Series'), { target: { value: 'Axis Saga' } })
            const row = screen.getByLabelText('Number for Book A').closest('[data-testid="bulk-series-row"]')
            expect(within(row).getByText('Book A')).toBeInTheDocument()
            expect(within(row).getByText('Ann Lee')).toBeInTheDocument()
        })

        it('keeps the list scrollable', () => {
            renderModal({ items: makeItems(20) })
            fireEvent.change(field('Series'), { target: { value: 'Axis Saga' } })
            const list = screen.getByTestId('bulk-series-rows')
            expect(list.style.maxHeight).toBe('260px')
            expect(list.style.overflowY).toBe('auto')
        })
    })

    // Issue #914: open with what the selected books share.
    describe('prefill (issue #914)', () => {
        const shared = (extra = {}) => makeItems(2, { author: 'Ann Lee', publisher: 'Orbit', published_year: 1999, ...extra })

        it('opens each shared field with its value and omits it from the patch when untouched', () => {
            const onSave = renderModal({ items: shared() })
            expect(field('Author')).toHaveValue('Ann Lee')
            expect(field('Publisher')).toHaveValue('Orbit')
            expect(field('Published Year')).toHaveValue(1999)
            expect(screen.getByRole('button', { name: 'Save to 2 Items' })).toBeDisabled()

            fireEvent.change(field('Author'), { target: { value: 'Ann Leigh' } })
            fireEvent.click(screen.getByRole('button', { name: 'Save to 2 Items' }))
            expect(onSave).toHaveBeenCalledWith([
                { key: 'k1', patch: { author: 'Ann Leigh' } },
                { key: 'k2', patch: { author: 'Ann Leigh' } },
            ])
        })

        it('a prefilled value the user clears means keep, not clear', () => {
            renderModal({ items: shared() })
            fireEvent.change(field('Author'), { target: { value: '' } })
            expect(screen.getByRole('button', { name: 'Save to 2 Items' })).toBeDisabled()
        })

        it('opens blank with the "Varies" placeholder where the books differ', () => {
            const items = makeItems(2)
            items[0].series_list = [{ name: 'Axis Saga', index: 1 }]
            renderModal({ items })
            for (const label of ['Author', 'Publisher', 'Published Year', 'Series']) {
                expect(field(label)).toHaveValue(label === 'Published Year' ? null : '')
                expect(field(label)).toHaveAttribute('placeholder', 'Varies — leave blank to keep each')
            }
        })

        it('keeps the plain placeholder where every book is empty for the field', () => {
            renderModal({ items: makeItems(2, { author: '', publisher: null, published_year: null }) })
            for (const label of ['Author', 'Publisher', 'Published Year', 'Series']) {
                expect(field(label)).toHaveAttribute('placeholder', 'Leave blank to keep unchanged')
            }
        })

        it('says "Varies" when only some books have a value', () => {
            const items = makeItems(2, { author: '' })
            items[0].author = 'Ann Lee'
            renderModal({ items })
            expect(field('Author')).toHaveValue('')
            expect(field('Author')).toHaveAttribute('placeholder', 'Varies — leave blank to keep each')
        })

        it('keeps the plain placeholder on a prefilled field', () => {
            renderModal({ items: shared() })
            expect(field('Author')).toHaveAttribute('placeholder', 'Leave blank to keep unchanged')
        })

        it('prefills a shared primary series, defaults to add mode and seeds each row', () => {
            const items = [
                { key: 'a', title: 'First', author: 'X', series_list: [{ name: 'Axis Saga', index: 1 }] },
                { key: 'b', title: 'Second', author: 'X', series_list: [{ name: 'Axis Saga', index: 2 }] },
                { key: 'c', title: 'Third', author: 'X', series_list: [{ name: 'Axis Saga' }] },
            ]
            const onSave = renderModal({ items })

            expect(field('Series')).toHaveValue('Axis Saga')
            expect(screen.getByRole('radio', { name: 'Add or update this series' })).toBeChecked()
            expect(screen.getByLabelText('Number for First')).toHaveValue(1)
            expect(screen.getByLabelText('Number for Second')).toHaveValue(2)
            expect(screen.getByLabelText('Number for Third')).toHaveValue(null)
            expect(screen.getByRole('button', { name: 'Save to 3 Items' })).toBeDisabled()

            fireEvent.change(screen.getByLabelText('Number for Third'), { target: { value: '3' } })
            fireEvent.click(screen.getByRole('button', { name: 'Save to 3 Items' }))
            expect(onSave).toHaveBeenCalledWith([
                { key: 'a', patch: {} },
                { key: 'b', patch: {} },
                { key: 'c', patch: { series_add: [{ name: 'Axis Saga', index: 3 }] } },
            ])
        })

        it('defaults to replace when the books share no series', () => {
            renderModal({ items: makeItems(2) })
            expect(screen.getByRole('radio', { name: 'Replace all series' })).toBeChecked()
        })
    })

    it('Cancel and Escape both close without saving', () => {
        const onSave = vi.fn()
        const onClose = vi.fn()
        render(<BulkMetadataEditModal open items={makeItems(1)} onSave={onSave} onClose={onClose} />)

        fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
        expect(onClose).toHaveBeenCalledTimes(1)

        fireEvent.keyDown(document, { key: 'Escape' })
        expect(onClose).toHaveBeenCalledTimes(2)
        expect(onSave).not.toHaveBeenCalled()
    })

    it('starts fresh again the next time it opens', () => {
        function Harness() {
            const [open, setOpen] = useState(true)
            return (
                <div>
                    <button onClick={() => setOpen(true)}>Reopen</button>
                    <BulkMetadataEditModal
                        open={open} items={makeItems(1)} onSave={vi.fn()} onClose={() => setOpen(false)}
                    />
                </div>
            )
        }
        render(<Harness />)

        fireEvent.change(field('Author'), { target: { value: 'Robin Hobb' } })
        fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
        fireEvent.click(screen.getByRole('button', { name: 'Reopen' }))

        expect(field('Author')).toHaveValue('Author 1')
    })
})
