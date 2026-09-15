import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import TranscriptionPage from './TranscriptionPage'
import { roleMeets } from '../roles'

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

// The real comparison, not a copy of it: these mocks used to reimplement
// `(ROLE_HIERARCHY[role] || 0) >= (ROLE_HIERARCHY[min] || 0)`, which is the
// fail-open form, so a typo'd minimum behaved the same here as in the app
// and the suite stayed green either way (issue #359).
vi.mock('../contexts/AuthContext', () => ({
    useAuth: () => ({
        hasMinRole: (min) => roleMeets(authRef.role, min),
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

// Resolves once the page has rendered the pair, not merely requested it. The
// page calls getPairs synchronously on mount and shows only a spinner until the
// request settles, so waiting on the mock let the "no such button" assertions
// below run against the spinner and pass whatever the gating did. Plural for
// the same reason as below: desktop and mobile paths both mount under jsdom.
async function renderPage(tab = 'not-transcribed') {
    const utils = render(
        <MemoryRouter>
            <TranscriptionPage tab={tab} />
        </MemoryRouter>,
    )
    await screen.findAllByText('Dune')
    return utils
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
        await renderPage()
        expect(screen.queryByRole('button', { name: QUEUE_ACTION })).toBeNull()
    })

    it('offers no Queue control to an editor either', async () => {
        // POST /api/transcription/queue/batch is admin; editors get Cancel only.
        authRef.role = 'editor'
        await renderPage()
        expect(screen.queryByRole('button', { name: QUEUE_ACTION })).toBeNull()
    })

    it('offers it to an admin', async () => {
        authRef.role = 'admin'
        await renderPage()
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
        await renderPage('in-progress')
        expect(screen.queryByRole('button', { name: /cancel/i })).toBeNull()
    })
})
