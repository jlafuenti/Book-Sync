import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import TranscriptionPage from './TranscriptionPage'

/**
 * Issue #562: the mobile In Progress section rendered the running queue item
 * as one card and then every `transcribing` pair as another, so the book being
 * transcribed appeared twice while the header still counted one.
 *
 * Each book must appear once. The active queue item, when there is one,
 * supplies the live progress and message for its pair's card; a transcribing
 * pair without a queue item still gets a card; and a queue item whose pair has
 * not yet flipped to `transcribing` (the two polls lag each other) still shows.
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

vi.mock('../hooks/useIsMobile', () => ({ default: () => true }))

// Shaped like BookPairResponse from GET /api/pairs; values are synthetic.
function media(id, title, author, overrides = {}) {
    return {
        id,
        title,
        author,
        series: null,
        series_index: null,
        format: 'epub',
        cover_path: null,
        ...overrides,
    }
}

function pair(id, title, author, status) {
    return {
        id,
        ebook: media(id + 100, title, author),
        audiobook: media(id + 200, title, author, { format: 'm4b', duration_seconds: 3600 }),
        status,
        matched_at: '2026-09-01T10:00:00',
        synced_at: null,
        acknowledged: false,
        sync_map_version: null,
    }
}

// Shaped like QueueItemResponse from GET /api/transcription/queue.
function queueItem(overrides = {}) {
    return {
        id: 50,
        book_pair_id: 1,
        book_title: 'Axis Test',
        status: 'in_progress',
        priority: 100,
        position: 0,
        progress: 0.42,
        message: 'Transcribing chunk 5 of 12',
        error_message: null,
        retry_count: 0,
        created_at: '2026-09-15T08:00:00',
        started_at: '2026-09-15T08:01:00',
        completed_at: null,
        force_run: false,
        paused_at: null,
        ...overrides,
    }
}

beforeEach(() => {
    getPairsMock.mockReset().mockResolvedValue([])
    getQueueMock.mockReset().mockResolvedValue([])
    statusMock.mockReset().mockResolvedValue({ status: 'transcribing' })
})

async function renderPage() {
    const utils = render(
        <MemoryRouter>
            <TranscriptionPage />
        </MemoryRouter>,
    )
    await screen.findByPlaceholderText('Search transcriptions...')
    return utils
}

const cards = (container) => container.querySelectorAll('.tx-mobile-active-card')
const header = () => screen.getByText(/^In Progress \(\d+\)$/)

describe('mobile In Progress section (issue #562)', () => {
    it('renders the running job once, with the queue\'s live progress', async () => {
        getPairsMock.mockResolvedValue([pair(1, 'Axis Test', 'A. Author', 'transcribing')])
        getQueueMock.mockResolvedValue([queueItem()])

        const { container } = await renderPage()

        expect(cards(container)).toHaveLength(1)
        expect(within(cards(container)[0]).getByText('42% Synced')).toBeInTheDocument()
        expect(header()).toHaveTextContent('In Progress (1)')
    })

    it('shows the queue\'s message on that single card before progress arrives', async () => {
        getPairsMock.mockResolvedValue([pair(1, 'Axis Test', 'A. Author', 'transcribing')])
        getQueueMock.mockResolvedValue([queueItem({ progress: null })])

        const { container } = await renderPage()

        expect(cards(container)).toHaveLength(1)
        expect(screen.getByText('Transcribing chunk 5 of 12')).toBeInTheDocument()
        expect(screen.queryByText('Transcribing...')).not.toBeInTheDocument()
    })

    it('still renders a transcribing pair that has no queue item', async () => {
        getPairsMock.mockResolvedValue([pair(2, 'Bartleby Sample', 'H. Writer', 'transcribing')])

        const { container } = await renderPage()

        expect(cards(container)).toHaveLength(1)
        expect(within(cards(container)[0]).getByText('Bartleby Sample')).toBeInTheDocument()
        expect(header()).toHaveTextContent('In Progress (1)')
    })

    it('renders the active job once when its pair has not flipped to transcribing yet', async () => {
        getPairsMock.mockResolvedValue([pair(1, 'Axis Test', 'A. Author', 'manual_matched')])
        getQueueMock.mockResolvedValue([queueItem()])

        const { container } = await renderPage()

        expect(cards(container)).toHaveLength(1)
        expect(within(cards(container)[0]).getByText('42% Synced')).toBeInTheDocument()
        expect(header()).toHaveTextContent('In Progress (1)')
    })

    it('counts one card per book when the job and another transcribing pair coexist', async () => {
        getPairsMock.mockResolvedValue([
            pair(1, 'Axis Test', 'A. Author', 'transcribing'),
            pair(2, 'Bartleby Sample', 'H. Writer', 'transcribing'),
        ])
        getQueueMock.mockResolvedValue([queueItem()])

        const { container } = await renderPage()

        expect(cards(container)).toHaveLength(2)
        expect(header()).toHaveTextContent('In Progress (2)')
    })

    it('filters cards out and back in with the mobile search', async () => {
        getPairsMock.mockResolvedValue([
            pair(1, 'Axis Test', 'A. Author', 'transcribing'),
            pair(2, 'Bartleby Sample', 'H. Writer', 'transcribing'),
        ])
        getQueueMock.mockResolvedValue([queueItem()])

        const { container } = await renderPage()
        const search = screen.getByPlaceholderText('Search transcriptions...')

        fireEvent.change(search, { target: { value: 'bartleby' } })
        expect(cards(container)).toHaveLength(1)
        expect(within(cards(container)[0]).getByText('Bartleby Sample')).toBeInTheDocument()
        expect(header()).toHaveTextContent('In Progress (1)')

        fireEvent.change(search, { target: { value: 'axis' } })
        expect(cards(container)).toHaveLength(1)
        expect(within(cards(container)[0]).getByText('42% Synced')).toBeInTheDocument()

        fireEvent.change(search, { target: { value: 'nothing matches' } })
        expect(cards(container)).toHaveLength(0)
        expect(screen.getByText('No matches')).toBeInTheDocument()
        expect(header()).toHaveTextContent('In Progress (0)')

        fireEvent.change(search, { target: { value: '' } })
        expect(cards(container)).toHaveLength(2)
        expect(header()).toHaveTextContent('In Progress (2)')
    })

    // The fix moved the shared title/author/series matcher out of the component;
    // the Queued section's search goes through it too.
    it('still searches the Queued section by queue title and by pair fields', async () => {
        getPairsMock.mockResolvedValue([pair(3, 'Axis Test Two', 'C. Scribe', 'manual_matched')])
        getQueueMock.mockResolvedValue([
            queueItem({ id: 51, book_pair_id: 3, book_title: 'Queued Axis', status: 'pending', progress: null }),
        ])

        await renderPage()
        const search = screen.getByPlaceholderText('Search transcriptions...')
        const queued = () => screen.getByText(/^Queued \(\d+\)$/)

        fireEvent.change(search, { target: { value: 'queued axis' } })
        expect(queued()).toHaveTextContent('Queued (1)')

        fireEvent.change(search, { target: { value: 'scribe' } })
        expect(queued()).toHaveTextContent('Queued (1)')

        fireEvent.change(search, { target: { value: 'nothing matches' } })
        expect(queued()).toHaveTextContent('Queued (0)')
    })
})
