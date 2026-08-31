import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import TranscriptionQueuePage from './TranscriptionQueuePage'

// The page talks to the API module directly and polls on a timer, so mock the
// module and keep the fake timers out of it (real timers, one 3s interval that
// never fires within a test).
const {
    getQueueMock, getHistoryMock, removeMock, cancelMock, priorityMock,
    runNowMock, offHoursMock, authRef,
} = vi.hoisted(() => ({
    getQueueMock: vi.fn(),
    getHistoryMock: vi.fn(),
    removeMock: vi.fn(),
    cancelMock: vi.fn(),
    priorityMock: vi.fn(),
    runNowMock: vi.fn(),
    offHoursMock: vi.fn(),
    authRef: { role: 'admin' },
}))

vi.mock('../api', async (importOriginal) => {
    const actual = await importOriginal()
    return {
        ...actual,
        getTranscriptionQueue: getQueueMock,
        getQueueHistory: getHistoryMock,
        removeFromQueue: removeMock,
        cancelTranscription: cancelMock,
        updateQueuePriority: priorityMock,
        runQueueItemNow: runNowMock,
        getOffHoursStatus: offHoursMock,
    }
})

const ROLE_HIERARCHY = { superadmin: 4, admin: 3, editor: 2, user: 1 }
vi.mock('../contexts/AuthContext', () => ({
    useAuth: () => ({
        hasMinRole: (min) => (ROLE_HIERARCHY[authRef.role] || 0) >= (ROLE_HIERARCHY[min] || 0),
    }),
}))

function queueItem(overrides = {}) {
    return {
        id: 1,
        book_pair_id: 10,
        book_title: 'Dune',
        status: 'pending',
        priority: 100,
        position: 1,
        progress: 0,
        message: 'Waiting in queue',
        error_message: null,
        retry_count: 0,
        created_at: '2026-06-01T12:00:00',
        started_at: null,
        completed_at: null,
        force_run: false,
        paused_at: null,
        ...overrides,
    }
}

const WINDOW_CLOSED = {
    enabled: true, open: false, start: '01:00', end: '07:00',
    timezone: 'America/New_York', opens_at: '2026-06-02T01:00:00-04:00', closes_at: null,
}
const WINDOW_OPEN = {
    enabled: true, open: true, start: '01:00', end: '07:00',
    timezone: 'America/New_York', opens_at: null, closes_at: '2026-06-01T07:00:00-04:00',
}
const WINDOW_DISABLED = {
    enabled: false, open: true, start: '01:00', end: '07:00',
    timezone: 'UTC', opens_at: null, closes_at: null,
}

beforeEach(() => {
    authRef.role = 'admin'
    getQueueMock.mockReset().mockResolvedValue([])
    getHistoryMock.mockReset().mockResolvedValue([])
    removeMock.mockReset().mockResolvedValue({})
    cancelMock.mockReset().mockResolvedValue({})
    priorityMock.mockReset().mockResolvedValue({})
    runNowMock.mockReset().mockResolvedValue({})
    offHoursMock.mockReset().mockResolvedValue(WINDOW_DISABLED)
})

afterEach(() => {
    vi.restoreAllMocks()
})

describe('off-hours window banner', () => {
    it('says nothing when scheduling is off', async () => {
        render(<TranscriptionQueuePage />)
        await waitFor(() => expect(offHoursMock).toHaveBeenCalled())
        expect(screen.queryByText(/Off-hours scheduling/)).not.toBeInTheDocument()
    })

    it('explains the wait and names the opening time when the window is shut', async () => {
        offHoursMock.mockResolvedValue(WINDOW_CLOSED)
        render(<TranscriptionQueuePage />)

        const banner = await screen.findByText(/queued books wait until/)
        expect(banner).toHaveTextContent('01:00')
        expect(banner).toHaveTextContent('America/New_York')
    })

    it('says the queue is running and when it closes while the window is open', async () => {
        offHoursMock.mockResolvedValue(WINDOW_OPEN)
        render(<TranscriptionQueuePage />)

        const banner = await screen.findByText(/the queue is running now/)
        expect(banner).toHaveTextContent('07:00')
    })

    it('renders the queue normally if the window status call fails', async () => {
        offHoursMock.mockRejectedValue(new Error('boom'))
        getQueueMock.mockResolvedValue([queueItem()])
        render(<TranscriptionQueuePage />)

        expect(await screen.findByText('Dune')).toBeInTheDocument()
        expect(screen.queryByText(/Off-hours scheduling/)).not.toBeInTheDocument()
    })
})

describe('Run now', () => {
    it('offers Run now on waiting items while the window is shut', async () => {
        offHoursMock.mockResolvedValue(WINDOW_CLOSED)
        getQueueMock.mockResolvedValue([queueItem()])
        render(<TranscriptionQueuePage />)

        expect(await screen.findByRole('button', { name: /Run now/ })).toBeInTheDocument()
    })

    it('does not offer Run now while the window is already open', async () => {
        offHoursMock.mockResolvedValue(WINDOW_OPEN)
        getQueueMock.mockResolvedValue([queueItem()])
        render(<TranscriptionQueuePage />)

        await screen.findByText('Dune')
        expect(screen.queryByRole('button', { name: /Run now/ })).not.toBeInTheDocument()
    })

    it('does not offer Run now to non-admins', async () => {
        authRef.role = 'editor'
        offHoursMock.mockResolvedValue(WINDOW_CLOSED)
        getQueueMock.mockResolvedValue([queueItem()])
        render(<TranscriptionQueuePage />)

        await screen.findByText('Dune')
        expect(screen.queryByRole('button', { name: /Run now/ })).not.toBeInTheDocument()
    })

    it('calls the API and refreshes the queue', async () => {
        offHoursMock.mockResolvedValue(WINDOW_CLOSED)
        getQueueMock.mockResolvedValue([queueItem({ id: 42 })])
        render(<TranscriptionQueuePage />)

        fireEvent.click(await screen.findByRole('button', { name: /Run now/ }))

        await waitFor(() => expect(runNowMock).toHaveBeenCalledWith(42))
        await waitFor(() => expect(getQueueMock.mock.calls.length).toBeGreaterThan(1))
    })

    it('surfaces a failure instead of failing silently', async () => {
        offHoursMock.mockResolvedValue(WINDOW_CLOSED)
        getQueueMock.mockResolvedValue([queueItem()])
        runNowMock.mockRejectedValue(new Error('Cannot run a cancelled item'))
        render(<TranscriptionQueuePage />)

        fireEvent.click(await screen.findByRole('button', { name: /Run now/ }))

        expect(await screen.findByText(/Cannot run a cancelled item/)).toBeInTheDocument()
    })

    it('replaces the button with a marker once an item is forced', async () => {
        offHoursMock.mockResolvedValue(WINDOW_CLOSED)
        getQueueMock.mockResolvedValue([queueItem({ force_run: true })])
        render(<TranscriptionQueuePage />)

        expect(await screen.findByText(/Running next/)).toBeInTheDocument()
        expect(screen.queryByRole('button', { name: /Run now/ })).not.toBeInTheDocument()
    })
})

describe('paused items', () => {
    it('shows banked progress so a pause reads differently from "not started"', async () => {
        offHoursMock.mockResolvedValue(WINDOW_CLOSED)
        getQueueMock.mockResolvedValue([queueItem({
            progress: 0.43,
            paused_at: '2026-06-01T07:00:00',
            message: 'Paused for off-hours — resumes at 01:00',
        })])
        render(<TranscriptionQueuePage />)

        expect(await screen.findByText(/Paused — 43% done/)).toBeInTheDocument()
        expect(screen.getByText(/resumes at 01:00/)).toBeInTheDocument()
    })

    it('shows no pause chip for an item that never started', async () => {
        getQueueMock.mockResolvedValue([queueItem()])
        render(<TranscriptionQueuePage />)

        await screen.findByText('Dune')
        expect(screen.queryByText(/Paused/)).not.toBeInTheDocument()
    })
})

// ---------------------------------------------------------------------------
// Issue #312: the write controls on these pages were rendered to every role,
// and the server refuses them below admin -- or, for Cancel, below editor since
// #207 gave it a floor. A plain user got a confirm dialog, clicked through, and
// received a raw 403 in the error banner. The control looked available and was
// not.
//
// Cancel is the one that changed: before #207 it was the single write control a
// plain user could actually use, which was the bug #207 fixed. That left the UI
// advertising it to people who can no longer use it.
// ---------------------------------------------------------------------------

describe('Cancel is editor-gated (issue #312)', () => {
    const running = () => queueItem({ status: 'in_progress', message: 'Transcribing...' })

    it('is hidden from a plain user', async () => {
        authRef.role = 'user'
        getQueueMock.mockResolvedValue([running()])
        render(<TranscriptionQueuePage />)
        await screen.findByText('Dune')
        expect(screen.queryByText(/Cancel/)).toBeNull()
    })

    it('is offered to an editor', async () => {
        authRef.role = 'editor'
        getQueueMock.mockResolvedValue([running()])
        render(<TranscriptionQueuePage />)
        await screen.findByText('Dune')
        expect(screen.getByText(/Cancel/)).toBeInTheDocument()
    })

    it('is offered to an admin', async () => {
        authRef.role = 'admin'
        getQueueMock.mockResolvedValue([running()])
        render(<TranscriptionQueuePage />)
        await screen.findByText('Dune')
        expect(screen.getByText(/Cancel/)).toBeInTheDocument()
    })
})
