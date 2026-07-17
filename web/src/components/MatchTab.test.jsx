import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import MatchTab from './MatchTab'

const { searchMetadataMock } = vi.hoisted(() => ({ searchMetadataMock: vi.fn() }))

vi.mock('../api', async (importOriginal) => {
    const actual = await importOriginal()
    return { ...actual, searchMetadata: searchMetadataMock }
})

function fullResult(overrides = {}) {
    return {
        id: 'B002UZKL8W',
        title: 'The Name of the Wind',
        author: 'Patrick Rothfuss',
        series: 'The Kingkiller Chronicle',
        series_index: 1,
        publisher: 'Audible Studios',
        publish_year: 2009,
        description: 'Told in Kvothe\'s own voice, this is the tale.',
        genres: 'Fantasy, Epic',
        tags: 'Magic',
        language: 'English',
        narrators: 'Nick Podehl',
        isbn: null,
        asin: 'B002UZKL8W',
        cover_url: 'https://example.com/cover.jpg',
        ...overrides,
    }
}

beforeEach(() => {
    searchMetadataMock.mockReset().mockResolvedValue([fullResult()])
})

describe('MatchTab provider selection', () => {
    it('offers all four providers', () => {
        render(<MatchTab currentData={{}} onApply={vi.fn()} />)
        const select = screen.getByRole('combobox')
        const values = Array.from(select.options).map(o => o.value)
        expect(values).toEqual(expect.arrayContaining(['google', 'openlibrary', 'audible', 'hardcover']))
    })

    it('defaults to Audible for audiobooks', () => {
        render(<MatchTab currentData={{}} onApply={vi.fn()} bookType="audiobook" />)
        expect(screen.getByRole('combobox')).toHaveValue('audible')
    })

    it('keeps the Open Library default for ebooks', () => {
        render(<MatchTab currentData={{}} onApply={vi.fn()} bookType="ebook" />)
        expect(screen.getByRole('combobox')).toHaveValue('openlibrary')
    })
})

describe('MatchTab compare view', () => {
    async function searchAndSelect(currentData = {}) {
        render(<MatchTab currentData={currentData} onApply={vi.fn()} />)
        fireEvent.change(screen.getByRole('combobox'), { target: { value: 'audible' } })
        const inputs = screen.getAllByRole('textbox')
        fireEvent.change(inputs[0], { target: { value: 'name of the wind' } })
        fireEvent.click(screen.getByRole('button', { name: 'Search' }))
        fireEvent.click(await screen.findByRole('button', { name: 'Select' }))
    }

    it('shows series card info on search results', async () => {
        render(<MatchTab currentData={{}} onApply={vi.fn()} />)
        const inputs = screen.getAllByRole('textbox')
        fireEvent.change(inputs[0], { target: { value: 'name of the wind' } })
        fireEvent.click(screen.getByRole('button', { name: 'Search' }))
        expect(await screen.findByText(/The Kingkiller Chronicle #1/)).toBeInTheDocument()
    })

    it('renders series, genres, tags, narrators rows in the compare table', async () => {
        await searchAndSelect()
        expect(screen.getByText('series index')).toBeInTheDocument()
        expect(screen.getByText('The Kingkiller Chronicle')).toBeInTheDocument()
        expect(screen.getByText('Fantasy, Epic')).toBeInTheDocument()
        expect(screen.getByText('Nick Podehl')).toBeInTheDocument()
        expect(screen.getByText('English')).toBeInTheDocument()
    })

    it('auto-checks fields missing locally and applies them', async () => {
        const onApply = vi.fn()
        render(<MatchTab currentData={{ title: 'The Name of the Wind', author: 'Patrick Rothfuss' }} onApply={onApply} />)
        const inputs = screen.getAllByRole('textbox')
        fireEvent.change(inputs[0], { target: { value: 'name of the wind' } })
        fireEvent.click(screen.getByRole('button', { name: 'Search' }))
        fireEvent.click(await screen.findByRole('button', { name: 'Select' }))

        fireEvent.click(screen.getByRole('button', { name: 'Apply Selected' }))

        await waitFor(() => expect(onApply).toHaveBeenCalled())
        const payload = onApply.mock.calls[0][0]
        // Missing locally -> auto-checked and applied.
        expect(payload.series).toBe('The Kingkiller Chronicle')
        expect(payload.series_index).toBe(1)
        expect(payload.genres).toBe('Fantasy, Epic')
        expect(payload.tags).toBe('Magic')
        expect(payload.narrators).toBe('Nick Podehl')
        expect(payload.language).toBe('English')
        expect(payload.asin).toBe('B002UZKL8W')
        expect(payload.coverUrl).toBe('https://example.com/cover.jpg')
        // Present locally -> not auto-checked, so not in the payload.
        expect(payload.title).toBeUndefined()
        expect(payload.author).toBeUndefined()
    })

    it('does not offer fields the result lacks', async () => {
        searchMetadataMock.mockResolvedValue([fullResult({ series: null, series_index: null })])
        const onApply = vi.fn()
        render(<MatchTab currentData={{}} onApply={onApply} />)
        const inputs = screen.getAllByRole('textbox')
        fireEvent.change(inputs[0], { target: { value: 'x' } })
        fireEvent.click(screen.getByRole('button', { name: 'Search' }))
        fireEvent.click(await screen.findByRole('button', { name: 'Select' }))

        fireEvent.click(screen.getByRole('button', { name: 'Apply Selected' }))
        await waitFor(() => expect(onApply).toHaveBeenCalled())
        const payload = onApply.mock.calls[0][0]
        expect(payload.series).toBeUndefined()
        expect(payload.series_index).toBeUndefined()
    })
})
