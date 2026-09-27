import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import PrintPageFill from './PrintPageFill'

/**
 * Issue #739: the System page card that fills ebooks' print page counts from
 * Google Books. The job runs on the server in the background; the card starts
 * it, polls its status, and says how it ended.
 */

const { statusMock, startMock, cancelMock } = vi.hoisted(() => ({
    statusMock: vi.fn(),
    startMock: vi.fn(),
    cancelMock: vi.fn(),
}))

vi.mock('../api', async (importOriginal) => {
    const actual = await importOriginal()
    return {
        ...actual,
        getPrintPageFillStatus: statusMock,
        startPrintPageFill: startMock,
        cancelPrintPageFill: cancelMock,
    }
})

const IDLE = {
    running: false, current: 0, total: 0, found: 0, no_match: 0, errors: 0, remaining: 312,
    started_at: null, finished_at: null, cancel_requested: false,
    stopped_reason: null, message: null, last_error: null, api_key_configured: true,
}

beforeEach(() => {
    statusMock.mockReset().mockResolvedValue(IDLE)
    startMock.mockReset().mockResolvedValue({ status: 'started' })
    cancelMock.mockReset().mockResolvedValue({ status: 'cancel_requested' })
})

const startButton = () => screen.findByRole('button', { name: /look up print page counts/i })

describe('print page count fill', () => {
    it('says how many ebooks have no count yet', async () => {
        render(<PrintPageFill canEdit />)
        expect(await screen.findByText(/312 ebooks have no print page count/i)).toBeInTheDocument()
        expect(await startButton()).toBeEnabled()
    })

    it('starts the job, shows its progress, and then how it ended', async () => {
        render(<PrintPageFill canEdit pollMs={5} />)
        statusMock
            .mockResolvedValueOnce({ ...IDLE, running: true, current: 40, total: 312, found: 12, no_match: 28 })
            .mockResolvedValue({
                ...IDLE, current: 312, total: 312, found: 250, no_match: 60, errors: 2, remaining: 2,
                finished_at: '2026-09-27T12:00:00', message: 'Finished.',
            })

        fireEvent.click(await startButton())

        expect(startMock).toHaveBeenCalled()
        expect(await screen.findByText(/40 of 312/)).toBeInTheDocument()
        expect(screen.getByRole('button', { name: /cancel/i })).toBeInTheDocument()
        expect(await screen.findByText(/250 found, 60 not found, 2 errors/i)).toBeInTheDocument()
        expect(screen.getByText(/^Finished. 250 found/)).toBeInTheDocument()
        expect(screen.queryByRole('button', { name: /cancel/i })).toBeNull()
    })

    it('says to run it again tomorrow when the daily limit stopped it', async () => {
        statusMock.mockResolvedValue({
            ...IDLE, current: 900, total: 1586, found: 700, no_match: 200, remaining: 686,
            finished_at: '2026-09-27T12:00:00', stopped_reason: 'quota',
            message: "Stopped: Google Books' daily limit is reached. Run it again tomorrow to carry on.",
        })
        render(<PrintPageFill canEdit />)
        expect(await screen.findByText(/run it again tomorrow/i)).toBeInTheDocument()
    })

    it('cancels a running job', async () => {
        statusMock.mockResolvedValue({ ...IDLE, running: true, current: 5, total: 312 })
        render(<PrintPageFill canEdit pollMs={5} />)
        fireEvent.click(await screen.findByRole('button', { name: /cancel/i }))
        await waitFor(() => expect(cancelMock).toHaveBeenCalled())
    })

    it('shows why the status could not be read', async () => {
        statusMock.mockRejectedValue(new Error('Failed to load print page status'))
        render(<PrintPageFill canEdit />)
        expect(await screen.findByText(/failed to load print page status/i)).toBeInTheDocument()
    })

    it('shows why a cancel failed', async () => {
        statusMock.mockResolvedValue({ ...IDLE, running: true, current: 5, total: 312 })
        cancelMock.mockRejectedValue(new Error('Failed to cancel the lookup'))
        render(<PrintPageFill canEdit pollMs={1000} />)
        fireEvent.click(await screen.findByRole('button', { name: /cancel/i }))
        expect(await screen.findByText(/failed to cancel the lookup/i)).toBeInTheDocument()
    })

    it('shows why the job could not start', async () => {
        startMock.mockRejectedValue(new Error('Print page counts are already being looked up.'))
        render(<PrintPageFill canEdit />)
        fireEvent.click(await startButton())
        expect(await screen.findByText(/already being looked up/i)).toBeInTheDocument()
    })

    it('has nothing to start when every ebook has been handled', async () => {
        statusMock.mockResolvedValue({ ...IDLE, remaining: 0 })
        render(<PrintPageFill canEdit />)
        expect(await screen.findByText(/every ebook has a print page count or has been looked up/i)).toBeInTheDocument()
        expect(await startButton()).toBeDisabled()
    })

    it('warns when no Google Books API key is set', async () => {
        statusMock.mockResolvedValue({ ...IDLE, api_key_configured: false })
        render(<PrintPageFill canEdit />)
        expect(await screen.findByText(/no google books api key is set/i)).toBeInTheDocument()
    })

    it('does not warn when a key is set', async () => {
        render(<PrintPageFill canEdit />)
        await screen.findByText(/312 ebooks/i)
        expect(screen.queryByText(/no google books api key/i)).toBeNull()
    })

    it('shows a viewer the numbers but no controls', async () => {
        render(<PrintPageFill canEdit={false} />)
        expect(await screen.findByText(/312 ebooks have no print page count/i)).toBeInTheDocument()
        expect(screen.queryByRole('button', { name: /look up print page counts/i })).toBeNull()
    })
})
