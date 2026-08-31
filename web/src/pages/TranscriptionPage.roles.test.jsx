import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import TranscriptionPage from './TranscriptionPage'

/**
 * Issue #312: the transcription pages rendered write controls to every role.
 *
 * The server refuses these below admin — and, for Cancel, below editor since
 * #207 gave it a floor. So a plain user got a confirm dialog, clicked through,
 * and received a raw 403 in the error banner. The controls looked available and
 * were not.
 *
 * The route itself stays open deliberately: queue state is worth seeing if you
 * are waiting on your own book. What changes is that a reader is no longer
 * offered buttons that only ever fail.
 */

const {
    getPairsMock, getQueueMock, getHistoryMock, addToQueueMock,
    cancelMock, removeMock, priorityMock, statusMock, startMock, authRef,
} = vi.hoisted(() => ({
    getPairsMock: vi.fn(),
    getQueueMock: vi.fn(),
    getHistoryMock: vi.fn(),
    addToQueueMock: vi.fn(),
    cancelMock: vi.fn(),
    removeMock: vi.fn(),
    priorityMock: vi.fn(),
    statusMock: vi.fn(),
    startMock: vi.fn(),
    authRef: { role: 'admin' },
}))

vi.mock('../api', async (importOriginal) => {
    const actual = await importOriginal()
    return {
        ...actual,
        getPairs: getPairsMock,
        getTranscriptionQueue: getQueueMock,
        getQueueHistory: getHistoryMock,
        addToQueue: addToQueueMock,
        cancelTranscription: cancelMock,
        removeFromQueue: removeMock,
        updateQueuePriority: priorityMock,
        getTranscriptionStatus: statusMock,
        startTranscription: startMock,
    }
})

const ROLE_HIERARCHY = { superadmin: 4, admin: 3, editor: 2, user: 1 }
vi.mock('../contexts/AuthContext', () => ({
    useAuth: () => ({
        hasMinRole: (min) => (ROLE_HIERARCHY[authRef.role] || 0) >= (ROLE_HIERARCHY[min] || 0),
    }),
}))

function pair(overrides = {}) {
    return {
        id: 10,
        status: 'manual_matched',
        ebook: { id: 1, title: 'Dune', author: 'Frank Herbert', format: 'epub' },
        audiobook: { id: 2, title: 'Dune', author: 'Frank Herbert', format: 'm4b' },
        has_transcript: false,
        ...overrides,
    }
}

// The tab bar renders a button reading "Queue (0)", which is navigation, not a
// write control -- the route stays readable for everyone on purpose. Match the
// action buttons exactly ("Queue", "+ Queue", "Queue All 3") so the tab cannot
// make this assertion pass or fail for the wrong reason.
const QUEUE_ACTION = /^\+? ?Queue( All \d+)?$/i

function renderPage(tab = 'not-transcribed') {
    return render(
        <MemoryRouter>
            <TranscriptionPage tab={tab} />
        </MemoryRouter>,
    )
}

beforeEach(() => {
    authRef.role = 'admin'
    getPairsMock.mockReset().mockResolvedValue([pair()])
    getQueueMock.mockReset().mockResolvedValue([])
    getHistoryMock.mockReset().mockResolvedValue([])
    addToQueueMock.mockReset().mockResolvedValue({})
    cancelMock.mockReset().mockResolvedValue({})
    removeMock.mockReset().mockResolvedValue({})
    priorityMock.mockReset().mockResolvedValue({})
    statusMock.mockReset().mockResolvedValue({})
    startMock.mockReset().mockResolvedValue({})
})

describe('queueing is admin-gated', () => {
    it('offers no Queue control to a plain user', async () => {
        authRef.role = 'user'
        renderPage()
        await waitFor(() => expect(getPairsMock).toHaveBeenCalled())
        expect(screen.queryByRole('button', { name: QUEUE_ACTION })).toBeNull()
    })

    it('offers no Queue control to an editor either', async () => {
        // POST /api/transcription/queue/batch is admin; editors get Cancel only.
        authRef.role = 'editor'
        renderPage()
        await waitFor(() => expect(getPairsMock).toHaveBeenCalled())
        expect(screen.queryByRole('button', { name: QUEUE_ACTION })).toBeNull()
    })

    it('offers it to an admin', async () => {
        authRef.role = 'admin'
        renderPage()
        await waitFor(() => expect(getPairsMock).toHaveBeenCalled())
        // Plural: the desktop and mobile render paths both mount under jsdom, so
        // an admin legitimately has more than one of these on the page.
        expect((await screen.findAllByRole('button', { name: QUEUE_ACTION })).length)
            .toBeGreaterThan(0)
    })
})

describe('cancel is editor-gated', () => {
    const inProgress = () => pair({ status: 'transcribing' })

    it('is hidden from a plain user', async () => {
        authRef.role = 'user'
        getPairsMock.mockResolvedValue([inProgress()])
        renderPage('in-progress')
        await waitFor(() => expect(getPairsMock).toHaveBeenCalled())
        expect(screen.queryByRole('button', { name: /cancel/i })).toBeNull()
    })
})
