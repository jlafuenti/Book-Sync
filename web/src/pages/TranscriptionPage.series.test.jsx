import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import TranscriptionPage from './TranscriptionPage'

/**
 * Issue #907: a book can be in several series. The Transcription page filters,
 * searches, sorts and groups on every one of them, and a pair's series are the
 * union of its ebook's and its audiobook's.
 */

const { getPairsMock, getQueueMock, statusMock } = vi.hoisted(() => ({
    getPairsMock: vi.fn(),
    getQueueMock: vi.fn(),
    statusMock: vi.fn(),
}))

vi.mock('../api', async (importOriginal) => {
    const actual = await importOriginal()
    return {
        ...actual,
        getPairs: getPairsMock,
        getTranscriptionQueue: getQueueMock,
        getTranscriptionStatus: statusMock,
    }
})

vi.mock('../contexts/AuthContext', () => ({
    useAuth: () => ({ hasMinRole: () => true }),
}))

const { isMobileMock } = vi.hoisted(() => ({ isMobileMock: vi.fn(() => false) }))
vi.mock('../hooks/useIsMobile', () => ({ default: () => isMobileMock() }))

function pair(id, title, ebookSeries, audiobookSeries = []) {
    return {
        id,
        ebook: { id: id + 100, title, author: 'A. Author', series_list: ebookSeries, format: 'epub', cover_path: null },
        audiobook: { id: id + 200, title, author: 'A. Author', series_list: audiobookSeries, format: 'm4b', cover_path: null, duration_seconds: 3600 },
        status: 'auto_matched',
        matched_at: '2026-09-01T10:00:00',
        synced_at: null,
        acknowledged: false,
        sync_map_version: null,
    }
}

const SAGA = (index) => ({ name: 'Axis Saga', index })
const CHRONO = (index) => ({ name: 'Axis Chronological', index })

const PAIRS = [
    pair(1, 'Both Series', [SAGA(1), CHRONO(3)]),
    pair(2, 'Saga Only', [SAGA(2)]),
    pair(3, 'Audio Adds Series', [], [CHRONO(1)]),
    pair(4, 'No Series', []),
]

beforeEach(() => {
    isMobileMock.mockReset().mockReturnValue(false)
    getPairsMock.mockReset().mockResolvedValue(PAIRS)
    getQueueMock.mockReset().mockResolvedValue([])
    statusMock.mockReset().mockResolvedValue({ status: 'transcribing' })
})

async function renderPage(url = '/') {
    const utils = render(
        <MemoryRouter initialEntries={[url]}>
            <TranscriptionPage />
        </MemoryRouter>,
    )
    await screen.findByText('Both Series')
    return utils
}

const cardTitles = () => [...document.querySelectorAll('.transcription-card-title')].map(n => n.textContent)

describe('Transcription series filter and search (issue #907)', () => {
    it('lists every series name, secondary and audiobook-only included, as filter options', async () => {
        await renderPage()
        fireEvent.click(screen.getByText('Series', { selector: '.filter-pill-btn' }))

        const options = [...document.querySelectorAll('.filter-pill-dropdown .filter-pill-option')].map(n => n.textContent)
        expect(options).toEqual(['Axis Chronological', 'Axis Saga'])
    })

    it('a secondary series filters to every pair that is in it', async () => {
        await renderPage()
        fireEvent.click(screen.getByText('Series', { selector: '.filter-pill-btn' }))
        fireEvent.click(screen.getByText('Axis Chronological'))

        expect(cardTitles().sort()).toEqual(['Audio Adds Series', 'Both Series'])
    })

    it('the search box matches a secondary series name', async () => {
        await renderPage('/?search=chronolog')
        expect(cardTitles().sort()).toEqual(['Audio Adds Series', 'Both Series'])
    })

    it('sorting by series uses the primary series name', async () => {
        getPairsMock.mockResolvedValue([
            pair(1, 'Zed Book', [{ name: 'Zed Saga', index: 1 }, { name: 'Alpha Saga', index: 1 }]),
            pair(2, 'Beta Book', [{ name: 'Beta Saga', index: 1 }]),
            pair(3, 'Loose Book', []),
        ])
        render(<MemoryRouter><TranscriptionPage /></MemoryRouter>)
        await screen.findByText('Zed Book')

        fireEvent.click(screen.getByText(/^Sort:/))
        fireEvent.click(screen.getByText('Series', { selector: '.transcription-sort-option' }))

        // no series sorts first (empty string), then Beta Saga, then Zed Saga (the primary of Zed Book)
        expect(cardTitles()).toEqual(['Loose Book', 'Beta Book', 'Zed Book'])
    })
})

describe('Transcription series view (issue #907)', () => {
    it('shows a book under every series it is in, with that series\' number', async () => {
        await renderPage()
        fireEvent.click(screen.getByTitle('Series view'))

        const names = [...document.querySelectorAll('.transcription-series-title')].map(n => n.textContent)
        expect(names).toEqual(['Axis Chronological', 'Axis Saga', 'No Series'])

        // expand the two real series
        fireEvent.click(screen.getByText('Axis Chronological'))
        fireEvent.click(screen.getByText('Axis Saga'))
        const cards = [...document.querySelectorAll('.transcription-series-card')]
        const rows = (card) => [...card.querySelectorAll('.transcription-book-row')].map(r => [
            r.querySelector('.transcription-book-index').textContent,
            r.querySelector('.transcription-book-title').textContent,
        ])
        expect(rows(cards[0])).toEqual([['#1', 'Audio Adds Series'], ['#3', 'Both Series']])
        expect(rows(cards[1])).toEqual([['#1', 'Both Series'], ['#2', 'Saga Only']])
    })
})

describe('Transcription mobile search (issue #907)', () => {
    it('matches a secondary series name', async () => {
        isMobileMock.mockReturnValue(true)
        getPairsMock.mockResolvedValue([
            { ...pair(1, 'Both Series', [SAGA(1), CHRONO(3)]), status: 'transcribing' },
            { ...pair(2, 'Saga Only', [SAGA(2)]), status: 'transcribing' },
        ])
        render(<MemoryRouter><TranscriptionPage /></MemoryRouter>)
        const search = await screen.findByPlaceholderText('Search transcriptions...')

        fireEvent.change(search, { target: { value: 'chronolog' } })

        expect(screen.getByText('In Progress (1)')).toBeInTheDocument()
    })
})
