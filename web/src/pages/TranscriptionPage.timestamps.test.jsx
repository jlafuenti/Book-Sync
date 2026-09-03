import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import TranscriptionPage from './TranscriptionPage'
import { formatDate } from '../lib/datetime'

// Issue #216: `synced_at` is naive UTC on the wire. Read as local time it
// lands on the wrong calendar day for part of every day west of UTC, so the
// "synced" date on the Transcribed tab must go through the shared parser.

const { getPairsMock, getQueueMock, getHistoryMock, statusMock } = vi.hoisted(() => ({
    getPairsMock: vi.fn(),
    getQueueMock: vi.fn(),
    getHistoryMock: vi.fn(),
    statusMock: vi.fn(),
}))

vi.mock('../api', async (importOriginal) => {
    const actual = await importOriginal()
    return {
        ...actual,
        getPairs: getPairsMock,
        getTranscriptionQueue: getQueueMock,
        getQueueHistory: getHistoryMock,
        getTranscriptionStatus: statusMock,
    }
})

vi.mock('../contexts/AuthContext', () => ({
    useAuth: () => ({ hasMinRole: () => true }),
}))

// 02:30 UTC is the previous day in every zone west of UTC — the exact case
// the old local parse got wrong.
const SYNCED_AT = '2026-08-22T02:30:00'

beforeEach(() => {
    getPairsMock.mockReset().mockResolvedValue([{
        id: 10,
        status: 'synced',
        match_type: 'manual',
        synced_at: SYNCED_AT,
        has_transcript: true,
        ebook: { id: 1, title: 'Dune', author: 'Frank Herbert', format: 'epub' },
        audiobook: { id: 2, title: 'Dune', author: 'Frank Herbert', format: 'm4b' },
    }])
    getQueueMock.mockReset().mockResolvedValue([])
    getHistoryMock.mockReset().mockResolvedValue([])
    statusMock.mockReset().mockResolvedValue({ status: 'idle' })
})

describe('TranscriptionPage synced_at (issue #216)', () => {
    it('shows the synced date as UTC in both the grid card and the list row', async () => {
        render(
            <MemoryRouter>
                <TranscriptionPage tab="transcribed" />
            </MemoryRouter>,
        )

        await screen.findByText('Dune')
        const expected = formatDate(SYNCED_AT)
        expect(screen.getByText(expected)).toBeInTheDocument()

        // Same value in the list view.
        fireEvent.click(screen.getByTitle('List view'))
        expect(screen.getByText(expected)).toBeInTheDocument()

        if (new Date(2026, 7, 22).getTimezoneOffset() > 0) {
            // West of UTC the local parse would have shown the next day.
            expect(screen.queryByText(new Date(SYNCED_AT).toLocaleDateString())).toBeNull()
        }
    })
})
