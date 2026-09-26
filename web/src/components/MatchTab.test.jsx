import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import MatchTab from './MatchTab'

const { searchMetadataMock, getSettingsMock } = vi.hoisted(() => ({
    searchMetadataMock: vi.fn(),
    getSettingsMock: vi.fn(),
}))

vi.mock('../api', async (importOriginal) => {
    const actual = await importOriginal()
    return { ...actual, searchMetadata: searchMetadataMock, getSettings: getSettingsMock }
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
    // Issue #263: below admin, GET /api/settings/ returns only
    // { abs_enabled, hardcover_configured }. MatchTab renders for editors, so
    // it reads the derived boolean, never the masked token string.
    getSettingsMock.mockReset().mockResolvedValue({ abs_enabled: false, hardcover_configured: false })
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

    it('defaults ebooks to Google Books when Hardcover is not configured', async () => {
        render(<MatchTab currentData={{}} onApply={vi.fn()} bookType="ebook" />)
        await waitFor(() => expect(getSettingsMock).toHaveBeenCalled())
        expect(screen.getByRole('combobox')).toHaveValue('google')
    })

    it('defaults ebooks to Hardcover when a token is configured', async () => {
        getSettingsMock.mockResolvedValue({ abs_enabled: false, hardcover_configured: true })
        render(<MatchTab currentData={{}} onApply={vi.fn()} bookType="ebook" />)
        await waitFor(() => expect(screen.getByRole('combobox')).toHaveValue('hardcover'))
    })

    it('reads the derived boolean, not the masked token (issue #263)', async () => {
        // A non-admin no longer receives `hardcover_api_token` at all. If the
        // component went back to reading it, this payload would leave the
        // provider on Google even though a token is configured.
        getSettingsMock.mockResolvedValue({ hardcover_configured: true })
        render(<MatchTab currentData={{}} onApply={vi.fn()} bookType="ebook" />)
        await waitFor(() => expect(screen.getByRole('combobox')).toHaveValue('hardcover'))
    })

    it('does not clobber a manual provider choice when settings load late', async () => {
        let resolveSettings
        getSettingsMock.mockReturnValue(new Promise(res => { resolveSettings = res }))
        render(<MatchTab currentData={{}} onApply={vi.fn()} bookType="ebook" />)

        fireEvent.change(screen.getByRole('combobox'), { target: { value: 'openlibrary' } })
        resolveSettings({ hardcover_configured: true })

        await waitFor(() => expect(getSettingsMock).toHaveBeenCalled())
        expect(screen.getByRole('combobox')).toHaveValue('openlibrary')
    })

    it('does not fetch settings for audiobooks', async () => {
        render(<MatchTab currentData={{}} onApply={vi.fn()} bookType="audiobook" />)
        await waitFor(() => expect(screen.getByRole('combobox')).toHaveValue('audible'))
        expect(getSettingsMock).not.toHaveBeenCalled()
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

// Issue #730: Google Books' page_count compares against the ebook's
// print_page_count under its own key (Ruling R1).
describe('MatchTab print page count compare row (issue #730)', () => {
    it('shows a compare row for a result with page_count and applies it as page_count', async () => {
        searchMetadataMock.mockResolvedValue([fullResult({ page_count: 412 })])
        const onApply = vi.fn()
        render(<MatchTab currentData={{ print_page_count: null }} onApply={onApply} bookType="ebook" />)
        const inputs = screen.getAllByRole('textbox')
        fireEvent.change(inputs[0], { target: { value: 'axis test' } })
        fireEvent.click(screen.getByRole('button', { name: 'Search' }))
        fireEvent.click(await screen.findByRole('button', { name: 'Select' }))

        expect(screen.getByText('412')).toBeInTheDocument()

        fireEvent.click(screen.getByRole('button', { name: 'Apply Selected' }))

        await waitFor(() => expect(onApply).toHaveBeenCalled())
        expect(onApply.mock.calls[0][0].page_count).toBe(412)
    })

    it('shows the current value from print_page_count, not page_count, and does not auto-tick a manual value', async () => {
        searchMetadataMock.mockResolvedValue([fullResult({ page_count: 412 })])
        const onApply = vi.fn()
        render(<MatchTab currentData={{ print_page_count: 200 }} onApply={onApply} bookType="ebook" />)
        const inputs = screen.getAllByRole('textbox')
        fireEvent.change(inputs[0], { target: { value: 'axis test' } })
        fireEvent.click(screen.getByRole('button', { name: 'Search' }))
        fireEvent.click(await screen.findByRole('button', { name: 'Select' }))

        expect(screen.getByText('200')).toBeInTheDocument()

        // A manually-set 200 is present locally, so the smart default must
        // leave the page_count row unticked — applying without touching any
        // checkbox must not overwrite it with the remote 412.
        fireEvent.click(screen.getByRole('button', { name: 'Apply Selected' }))

        await waitFor(() => expect(onApply).toHaveBeenCalled())
        expect(onApply.mock.calls[0][0].page_count).toBeUndefined()
    })

    it('does not show the row for audiobooks', async () => {
        searchMetadataMock.mockResolvedValue([fullResult({ page_count: 412 })])
        render(<MatchTab currentData={{}} onApply={vi.fn()} bookType="audiobook" />)
        const inputs = screen.getAllByRole('textbox')
        fireEvent.change(inputs[0], { target: { value: 'axis test' } })
        fireEvent.click(screen.getByRole('button', { name: 'Search' }))
        fireEvent.click(await screen.findByRole('button', { name: 'Select' }))

        expect(screen.queryByText('412')).toBeNull()
    })
})
