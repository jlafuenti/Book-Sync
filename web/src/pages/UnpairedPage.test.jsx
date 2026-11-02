import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import UnpairedPage, { BookRow } from './UnpairedPage'

const { coverSrcMock, getUnpairedMediaMock } = vi.hoisted(() => ({
    coverSrcMock: vi.fn(),
    getUnpairedMediaMock: vi.fn(),
}))

vi.mock('../api', () => ({
    coverSrc: coverSrcMock,
    getUnpairedMedia: getUnpairedMediaMock,
    createPair: vi.fn(),
}))

beforeEach(() => {
    coverSrcMock.mockReset()
    getUnpairedMediaMock.mockReset()
})

// Issue #120: the manual-pairing page loads only the unpaired set through the
// bounded `getUnpairedMedia()` helper (tab=unpaired on /library/items) — it no
// longer downloads every ebook, audiobook and pair to set-diff them itself.
describe('UnpairedPage data source', () => {
    it('lists the unpaired ebooks and audiobooks from getUnpairedMedia()', async () => {
        getUnpairedMediaMock.mockResolvedValue({
            ebooks: [{ id: 1, title: 'Lonely Ebook', author: 'A', filename: 'e.epub' }],
            audiobooks: [{ id: 2, title: 'Lonely Audio', author: 'B', filename: 'a.m4b' }],
        })
        render(<UnpairedPage />)

        expect(await screen.findByText('Lonely Ebook')).toBeInTheDocument()
        expect(screen.getByText('Lonely Audio')).toBeInTheDocument()
        expect(getUnpairedMediaMock).toHaveBeenCalledTimes(1)
    })
})

describe('UnpairedPage BookRow', () => {
    it('renders the resolved cover image when the item has a cover_path', async () => {
        coverSrcMock.mockResolvedValue('/api/files/covers/a.jpg?token=tok')
        render(<BookRow item={{ title: 'A Book', cover_path: '/api/files/covers/a.jpg', format: 'epub' }} onSelect={() => {}} />)

        const img = await screen.findByAltText('')
        expect(img).toHaveAttribute('src', '/api/files/covers/a.jpg?token=tok')
    })

    it('shows a placeholder when there is no cover_path', () => {
        render(<BookRow item={{ title: 'A Book', cover_path: null, format: 'epub' }} onSelect={() => {}} />)
        expect(screen.queryByRole('img')).not.toBeInTheDocument()
        expect(coverSrcMock).not.toHaveBeenCalled()
    })

    it('shows the primary series with +N (issue #907)', () => {
        render(<BookRow
            item={{ title: 'A Book', format: 'epub', series_list: [{ name: 'Axis Saga', index: 4 }, { name: 'Axis Chronological', index: 2 }] }}
            onSelect={() => {}}
        />)
        expect(screen.getByText('Axis Saga #4 +1')).toBeInTheDocument()
    })
})

// Issue #907: the series filter, its options and the text search see every
// series a book is in, not just the first.
describe('UnpairedPage series filter (issue #907)', () => {
    beforeEach(() => {
        getUnpairedMediaMock.mockResolvedValue({
            ebooks: [
                { id: 1, title: 'Both Series', format: 'epub', series_list: [{ name: 'Axis Saga', index: 1 }, { name: 'Axis Chronological', index: 3 }] },
                { id: 2, title: 'Saga Only', format: 'epub', series_list: [{ name: 'Axis Saga', index: 2 }] },
                { id: 3, title: 'No Series', format: 'epub', series_list: [] },
            ],
            audiobooks: [],
        })
    })

    it('offers every series name as a filter option', async () => {
        render(<UnpairedPage />)
        await screen.findByText('Both Series')

        const options = [...screen.getAllByRole('combobox')[1].querySelectorAll('option')].map(o => o.textContent)
        expect(options).toEqual(['All series', 'Axis Chronological', 'Axis Saga'])
    })

    it('a secondary series filters to the books that are in it', async () => {
        render(<UnpairedPage />)
        await screen.findByText('Both Series')

        fireEvent.change(screen.getAllByRole('combobox')[1], { target: { value: 'Axis Chronological' } })

        expect(screen.getAllByText('Both Series').length).toBeGreaterThan(0)
        expect(screen.queryByText('Saga Only')).toBeNull()
        expect(screen.queryByText('No Series')).toBeNull()
    })

    it('the text filter matches a secondary series name', async () => {
        render(<UnpairedPage />)
        await screen.findByText('Both Series')

        fireEvent.change(screen.getAllByPlaceholderText(/Filter both sides/)[0], { target: { value: 'chronolog' } })

        expect(screen.getAllByText('Both Series').length).toBeGreaterThan(0)
        expect(screen.queryByText('Saga Only')).toBeNull()
    })
})
