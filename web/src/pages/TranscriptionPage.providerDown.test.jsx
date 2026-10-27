import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import TranscriptionPage from './TranscriptionPage'
import { roleMeets } from '../roles'

/**
 * Issue #866: a worker that stayed down for two days failed the whole queue
 * one item at a time. The server now holds the queue instead, which is quiet
 * by design — so the page has to say why nothing is moving.
 */

const { getPairsMock, getQueueMock, providerStatusMock } = vi.hoisted(() => ({
    getPairsMock: vi.fn(),
    getQueueMock: vi.fn(),
    providerStatusMock: vi.fn(),
}))

vi.mock('../api', async (importOriginal) => {
    const actual = await importOriginal()
    return {
        ...actual,
        getPairs: getPairsMock,
        getTranscriptionQueue: getQueueMock,
        getProviderStatus: providerStatusMock,
    }
})

vi.mock('../contexts/AuthContext', () => ({
    useAuth: () => ({
        hasMinRole: (min) => roleMeets('admin', min),
    }),
}))

function renderPage() {
    return render(
        <MemoryRouter>
            <TranscriptionPage tab="queue" />
        </MemoryRouter>,
    )
}

beforeEach(() => {
    getPairsMock.mockReset().mockResolvedValue([])
    getQueueMock.mockReset().mockResolvedValue([])
    providerStatusMock.mockReset()
})

describe('Unreachable-worker banner (issue #866)', () => {
    it('says the queue is on hold while the worker is unreachable', async () => {
        providerStatusMock.mockResolvedValue({
            reachable: false,
            unreachable_since: '2026-06-01T03:00:00Z',
            next_probe_at: '2026-06-01T03:15:00Z',
        })

        renderPage()

        const banner = await screen.findByRole('status')
        expect(banner.textContent).toMatch(/Transcription worker unreachable since/)
        expect(banner.textContent).toMatch(/on hold/)
        expect(banner.textContent).toMatch(/nothing is being failed/)
    })

    it('shows nothing while the worker answers', async () => {
        providerStatusMock.mockResolvedValue({ reachable: true, unreachable_since: null, next_probe_at: null })

        renderPage()

        await waitFor(() => expect(providerStatusMock).toHaveBeenCalled())
        expect(screen.queryByText(/Transcription worker unreachable/)).toBeNull()
    })

    it('shows nothing when the status call itself fails', async () => {
        providerStatusMock.mockRejectedValue(new Error('Failed to fetch transcription worker status'))

        renderPage()

        await waitFor(() => expect(providerStatusMock).toHaveBeenCalled())
        expect(screen.queryByText(/Transcription worker unreachable/)).toBeNull()
        expect(screen.queryByText(/Failed to fetch transcription worker status/)).toBeNull()
    })
})
