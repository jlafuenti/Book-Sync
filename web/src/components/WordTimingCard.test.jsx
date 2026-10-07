import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import WordTimingCard from './WordTimingCard'

// Issue #835: System page card showing how many transcripts carry word-level
// timing, and queueing the rest for re-transcription. The page renders it for
// admins only (SystemPage.statusGate.test.jsx pins that); the endpoints are
// admin-only on the server too.

const { getStatusMock, queueMock } = vi.hoisted(() => ({
    getStatusMock: vi.fn(),
    queueMock: vi.fn(),
}))

vi.mock('../api', async (importOriginal) => {
    const actual = await importOriginal()
    return {
        ...actual,
        getWordTimingStatus: getStatusMock,
        queueWordTiming: queueMock,
    }
})

function httpError(statusCode, message) {
    const err = new Error(message)
    err.status = statusCode
    return err
}

beforeEach(() => {
    getStatusMock.mockReset().mockResolvedValue({ with_words: 7, without_words: 3, queued: 0 })
    queueMock.mockReset().mockResolvedValue({ queued: 3 })
})

describe('the status line', () => {
    it('shows the title and how many transcripts carry word timing', async () => {
        render(<WordTimingCard />)
        expect(await screen.findByText('Word timing')).toBeInTheDocument()
        expect(screen.getByText(/7 of 10 transcripts carry word timing\./)).toBeInTheDocument()
        expect(screen.getByText(/can only get it by being transcribed again/)).toBeInTheDocument()
    })

    it('says so, and offers no button, when every transcript has words', async () => {
        getStatusMock.mockResolvedValue({ with_words: 10, without_words: 0, queued: 0 })
        render(<WordTimingCard />)
        expect(await screen.findByText('Every transcript carries word timing.')).toBeInTheDocument()
        expect(screen.queryByRole('button')).not.toBeInTheDocument()
    })

    it('shows how many are already waiting in the queue', async () => {
        getStatusMock.mockResolvedValue({ with_words: 7, without_words: 3, queued: 2 })
        render(<WordTimingCard />)
        expect(await screen.findByText('2 waiting in the queue.')).toBeInTheDocument()
    })

    it('omits the waiting line when nothing is queued', async () => {
        render(<WordTimingCard />)
        await screen.findByText('Word timing')
        expect(screen.queryByText(/waiting in the queue/)).not.toBeInTheDocument()
    })

    it('renders nothing on a 404 (older server)', async () => {
        getStatusMock.mockRejectedValue(httpError(404, 'Not Found'))
        const { container } = render(<WordTimingCard />)
        await waitFor(() => expect(getStatusMock).toHaveBeenCalled())
        await waitFor(() => expect(container).toBeEmptyDOMElement())
    })

    it('shows the alert for any other load error', async () => {
        getStatusMock.mockRejectedValue(httpError(500, 'Boom'))
        render(<WordTimingCard />)
        expect(await screen.findByRole('alert')).toHaveTextContent('Boom')
        expect(screen.getByRole('alert')).toHaveClass('alert-error')
    })
})

describe('the queue button', () => {
    it('is shown when some transcripts lack words and are not queued yet', async () => {
        render(<WordTimingCard />)
        expect(await screen.findByRole('button', { name: 'Queue the rest for re-transcription' })).toHaveClass('btn-primary')
    })

    it('is hidden when everything without words is already queued', async () => {
        getStatusMock.mockResolvedValue({ with_words: 7, without_words: 3, queued: 3 })
        render(<WordTimingCard />)
        expect(await screen.findByText('3 waiting in the queue.')).toBeInTheDocument()
        expect(screen.queryByRole('button', { name: /Queue the rest/ })).not.toBeInTheDocument()
    })

    it('asks for confirmation naming the count, and posts nothing when cancelled', async () => {
        getStatusMock.mockResolvedValue({ with_words: 7, without_words: 5, queued: 2 })
        render(<WordTimingCard />)
        fireEvent.click(await screen.findByRole('button', { name: 'Queue the rest for re-transcription' }))
        const dialog = await screen.findByRole('dialog')
        // Issue #783: the panel comes from `.modal`.
        expect(dialog).toHaveClass('modal')
        expect(within(dialog).getByText(/Queue 3 books for re-transcription\?/)).toBeInTheDocument()
        expect(within(dialog).getByText(/behind everything already waiting/)).toBeInTheDocument()
        expect(within(dialog).getByText(/costs a full transcription/)).toBeInTheDocument()
        expect(queueMock).not.toHaveBeenCalled()

        fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
        await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
        expect(queueMock).not.toHaveBeenCalled()
    })

    it('closes the confirmation on Escape without queueing anything', async () => {
        render(<WordTimingCard />)
        fireEvent.click(await screen.findByRole('button', { name: 'Queue the rest for re-transcription' }))
        await screen.findByRole('dialog')
        fireEvent.keyDown(document, { key: 'Escape' })
        await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
        expect(queueMock).not.toHaveBeenCalled()
    })

    it('uses the singular for one book', async () => {
        getStatusMock.mockResolvedValue({ with_words: 9, without_words: 1, queued: 0 })
        render(<WordTimingCard />)
        fireEvent.click(await screen.findByRole('button', { name: 'Queue the rest for re-transcription' }))
        expect(within(await screen.findByRole('dialog')).getByText(/Queue 1 book for re-transcription\?/)).toBeInTheDocument()
    })

    it('queues on confirm, refreshes the status and shows the queued count', async () => {
        getStatusMock
            .mockResolvedValueOnce({ with_words: 7, without_words: 3, queued: 0 })
            .mockResolvedValue({ with_words: 7, without_words: 3, queued: 3 })
        render(<WordTimingCard />)
        fireEvent.click(await screen.findByRole('button', { name: 'Queue the rest for re-transcription' }))
        const dialog = await screen.findByRole('dialog')
        fireEvent.click(within(dialog).getByRole('button', { name: 'Queue them' }))

        await waitFor(() => expect(queueMock).toHaveBeenCalledTimes(1))
        expect(await screen.findByText('Queued 3 books.')).toBeInTheDocument()
        await waitFor(() => expect(getStatusMock).toHaveBeenCalledTimes(2))
        expect(await screen.findByText('3 waiting in the queue.')).toBeInTheDocument()
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
        expect(screen.queryByRole('button', { name: /Queue the rest/ })).not.toBeInTheDocument()
    })

    it('uses the singular in the result', async () => {
        queueMock.mockResolvedValue({ queued: 1 })
        render(<WordTimingCard />)
        fireEvent.click(await screen.findByRole('button', { name: 'Queue the rest for re-transcription' }))
        fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Queue them' }))
        expect(await screen.findByText('Queued 1 book.')).toBeInTheDocument()
    })

    it('shows the error when queueing fails, and does not claim success', async () => {
        queueMock.mockRejectedValue(httpError(500, 'Could not queue'))
        render(<WordTimingCard />)
        fireEvent.click(await screen.findByRole('button', { name: 'Queue the rest for re-transcription' }))
        fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Queue them' }))
        expect(await screen.findByRole('alert')).toHaveTextContent('Could not queue')
        expect(screen.queryByText(/^Queued/)).not.toBeInTheDocument()
    })
})
