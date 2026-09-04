import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import TranscriptionPage from './TranscriptionPage'
import { roleMeets } from '../roles'

/**
 * Issue #247: the Queue tab's History list showed a failed job's error and
 * offered nothing to do about it — the operator had to know to go to the book
 * page and press Start, or to the Troubleshoot page (which only lists pairs the
 * scan flagged).
 *
 * Retry is editor-gated because POST /api/transcription/queue/{id}/requeue is:
 * rendering it to a plain user shows a control whose only outcome is a 403.
 */

const {
    getPairsMock, getQueueMock, getHistoryMock, requeueMock, statusMock, authRef,
} = vi.hoisted(() => ({
    getPairsMock: vi.fn(),
    getQueueMock: vi.fn(),
    getHistoryMock: vi.fn(),
    requeueMock: vi.fn(),
    statusMock: vi.fn(),
    authRef: { role: 'admin' },
}))

vi.mock('../api', async (importOriginal) => {
    const actual = await importOriginal()
    return {
        ...actual,
        getPairs: getPairsMock,
        getTranscriptionQueue: getQueueMock,
        getQueueHistory: getHistoryMock,
        getTranscriptionStatus: statusMock,
        requeueQueueItem: requeueMock,
    }
})

// The real comparison, not a copy of it: these mocks used to reimplement
// `(ROLE_HIERARCHY[role] || 0) >= (ROLE_HIERARCHY[min] || 0)`, which is the
// fail-open form, so a typo'd minimum behaved the same here as in the app
// and the suite stayed green either way (issue #359).
vi.mock('../contexts/AuthContext', () => ({
    useAuth: () => ({
        hasMinRole: (min) => roleMeets(authRef.role, min),
    }),
}))

function historyItem(overrides = {}) {
    return {
        id: 7,
        book_pair_id: 10,
        book_title: 'Dune',
        status: 'failed',
        priority: 100,
        position: 0,
        progress: 0,
        message: null,
        error_message: 'whisper died',
        retry_count: 0,
        created_at: '2026-06-01T12:00:00',
        started_at: '2026-06-01T12:01:00',
        completed_at: '2026-06-01T12:09:00',
        force_run: false,
        paused_at: null,
        ...overrides,
    }
}

beforeEach(() => {
    authRef.role = 'admin'
    getPairsMock.mockReset().mockResolvedValue([])
    getQueueMock.mockReset().mockResolvedValue([])
    getHistoryMock.mockReset().mockResolvedValue([historyItem()])
    statusMock.mockReset().mockResolvedValue({ status: 'idle' })
    requeueMock.mockReset().mockResolvedValue({ id: 9, status: 'pending' })
})

// The History section is collapsed by default and its toggle is a div, not a
// button — grab it by class rather than by role.
async function openHistory() {
    const { container } = render(
        <MemoryRouter>
            <TranscriptionPage tab="queue" />
        </MemoryRouter>,
    )
    await waitFor(() => expect(getQueueMock).toHaveBeenCalled())
    fireEvent.click(container.querySelector('.transcription-history-toggle'))
    await screen.findByText('Dune')
    return container
}

describe('Retry on the Queue tab history (issue #247)', () => {
    it('offers Retry on a failed row, calls the API and refreshes both lists', async () => {
        await openHistory()
        const historyCallsBefore = getHistoryMock.mock.calls.length
        const queueCallsBefore = getQueueMock.mock.calls.length

        fireEvent.click(screen.getByRole('button', { name: /Retry/ }))

        await waitFor(() => expect(requeueMock).toHaveBeenCalledWith(7))
        // The failed row stays in History and a new pending row appears in the
        // queue, so both lists have to be re-read — and the history read must
        // bypass its "already loaded" cache or the table never updates.
        await waitFor(() => expect(getHistoryMock.mock.calls.length).toBeGreaterThan(historyCallsBefore))
        await waitFor(() => expect(getQueueMock.mock.calls.length).toBeGreaterThan(queueCallsBefore))
    })

    it('offers Retry on a cancelled row too', async () => {
        getHistoryMock.mockResolvedValue([historyItem({ status: 'cancelled', error_message: null })])
        await openHistory()

        expect(screen.getByRole('button', { name: /Retry/ })).toBeInTheDocument()
    })

    it('does not offer Retry on a completed row', async () => {
        getHistoryMock.mockResolvedValue([historyItem({ status: 'completed', error_message: null })])
        await openHistory()

        expect(screen.queryByRole('button', { name: /Retry/ })).not.toBeInTheDocument()
    })

    it('is hidden from a plain user, whom the server would refuse', async () => {
        authRef.role = 'user'
        await openHistory()

        expect(screen.queryByRole('button', { name: /Retry/ })).not.toBeInTheDocument()
    })

    it('is offered to an editor', async () => {
        authRef.role = 'editor'
        await openHistory()

        expect(screen.getByRole('button', { name: /Retry/ })).toBeInTheDocument()
    })

    it('surfaces a failure instead of failing silently', async () => {
        requeueMock.mockRejectedValue(new Error('Cannot retry a completed item'))
        await openHistory()

        fireEvent.click(screen.getByRole('button', { name: /Retry/ }))

        expect(await screen.findByText(/Cannot retry a completed item/)).toBeInTheDocument()
    })
})
