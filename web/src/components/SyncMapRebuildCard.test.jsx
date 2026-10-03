import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act, within } from '@testing-library/react'
import SyncMapRebuildCard from './SyncMapRebuildCard'

// Issue #774: System page card for rebuilding sync maps built by an older
// sentence splitter.

const { getStatusMock, startMock, cancelMock } = vi.hoisted(() => ({
    getStatusMock: vi.fn(),
    startMock: vi.fn(),
    cancelMock: vi.fn(),
}))

vi.mock('../api', async (importOriginal) => {
    const actual = await importOriginal()
    return {
        ...actual,
        getSyncMapRebuildStatus: getStatusMock,
        startSyncMapRebuild: startMock,
        cancelSyncMapRebuild: cancelMock,
    }
})

function status(overrides = {}) {
    return {
        current_version: 3,
        outdated: 0,
        total: 10,
        running: false,
        dry_run: false,
        started_at: null,
        finished_at: null,
        to_process: 0,
        processed: 0,
        succeeded: 0,
        failed: 0,
        skipped: 0,
        cancelled: false,
        results: [],
        ...overrides,
    }
}

function httpError(statusCode, message) {
    const err = new Error(message)
    err.status = statusCode
    return err
}

beforeEach(() => {
    getStatusMock.mockReset().mockResolvedValue(status())
    startMock.mockReset().mockResolvedValue(status({ running: true }))
    cancelMock.mockReset().mockResolvedValue(status({ running: false, cancelled: true }))
})

afterEach(() => {
    vi.useRealTimers()
})

describe('idle states', () => {
    it('shows one quiet line when every map is up to date', async () => {
        render(<SyncMapRebuildCard />)
        expect(await screen.findByText('All sync maps are up to date.')).toBeInTheDocument()
        expect(screen.queryByRole('button', { name: /Rebuild/ })).not.toBeInTheDocument()
        expect(screen.queryByRole('button', { name: 'Dry run' })).not.toBeInTheDocument()
    })

    it('shows the outdated count, the explanation and both buttons', async () => {
        getStatusMock.mockResolvedValue(status({ outdated: 4, total: 12 }))
        render(<SyncMapRebuildCard />)
        expect(await screen.findByText(/4 of 12 sync maps were built with older sentence handling/)).toBeInTheDocument()
        expect(screen.getByText(/re-aligns each book from its saved transcript/)).toBeInTheDocument()
        expect(screen.getByText(/ten seconds per book/)).toBeInTheDocument()
        expect(screen.getByRole('button', { name: 'Dry run' })).toHaveClass('btn-secondary')
        expect(screen.getByRole('button', { name: 'Rebuild 4 sync maps' })).toHaveClass('btn-primary')
    })

    it('uses the singular for one outdated map', async () => {
        getStatusMock.mockResolvedValue(status({ outdated: 1, total: 12 }))
        render(<SyncMapRebuildCard />)
        expect(await screen.findByRole('button', { name: 'Rebuild 1 sync map' })).toBeInTheDocument()
    })

    it('renders nothing on a 404 (older server)', async () => {
        getStatusMock.mockRejectedValue(httpError(404, 'Not Found'))
        const { container } = render(<SyncMapRebuildCard />)
        await waitFor(() => expect(getStatusMock).toHaveBeenCalled())
        await act(async () => {})
        expect(container).toBeEmptyDOMElement()
    })

    it('shows the alert for any other load error', async () => {
        getStatusMock.mockRejectedValue(httpError(500, 'Boom'))
        render(<SyncMapRebuildCard />)
        expect(await screen.findByRole('alert')).toHaveTextContent('Boom')
        expect(screen.getByRole('alert')).toHaveClass('alert-error')
    })

    it('shows the alert when the error carries no HTTP status', async () => {
        getStatusMock.mockRejectedValue(new Error('Network down'))
        render(<SyncMapRebuildCard />)
        expect(await screen.findByRole('alert')).toHaveTextContent('Network down')
    })
})

describe('starting a run', () => {
    beforeEach(() => {
        getStatusMock.mockResolvedValue(status({ outdated: 3, total: 9 }))
    })

    it('posts a dry run straight away', async () => {
        render(<SyncMapRebuildCard />)
        fireEvent.click(await screen.findByRole('button', { name: 'Dry run' }))
        await waitFor(() => expect(startMock).toHaveBeenCalledWith({ dryRun: true, pairIds: null }))
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })

    it('asks for confirmation before a real rebuild, then posts dry_run false', async () => {
        render(<SyncMapRebuildCard />)
        fireEvent.click(await screen.findByRole('button', { name: 'Rebuild 3 sync maps' }))
        const dialog = await screen.findByRole('dialog')
        // Issue #783: the panel comes from `.modal`; the old `modal-dialog` class was
        // never defined, so the dialog drew as a bare strip across the page.
        expect(dialog).toHaveClass('modal')
        expect(startMock).not.toHaveBeenCalled()
        expect(within(dialog).getByText(/Rebuild 3 sync maps\?/)).toBeInTheDocument()
        fireEvent.click(within(dialog).getByRole('button', { name: 'Rebuild' }))
        await waitFor(() => expect(startMock).toHaveBeenCalledWith({ dryRun: false, pairIds: null }))
        await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    })

    it('posts nothing when the confirmation is cancelled', async () => {
        render(<SyncMapRebuildCard />)
        fireEvent.click(await screen.findByRole('button', { name: 'Rebuild 3 sync maps' }))
        const dialog = await screen.findByRole('dialog')
        fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
        await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
        expect(startMock).not.toHaveBeenCalled()
    })

    it('closes the confirmation on Escape without starting anything', async () => {
        render(<SyncMapRebuildCard />)
        fireEvent.click(await screen.findByRole('button', { name: 'Rebuild 3 sync maps' }))
        await screen.findByRole('dialog')
        fireEvent.keyDown(document, { key: 'Escape' })
        await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
        expect(startMock).not.toHaveBeenCalled()
    })

    it('says a rebuild is already running on a 409, and refreshes the status', async () => {
        startMock.mockRejectedValue(httpError(409, 'Rebuild already active'))
        render(<SyncMapRebuildCard />)
        fireEvent.click(await screen.findByRole('button', { name: 'Dry run' }))
        expect(await screen.findByText('A rebuild is already running.')).toBeInTheDocument()
        await waitFor(() => expect(getStatusMock).toHaveBeenCalledTimes(2))
    })

    it('shows the server message when the start fails for another reason', async () => {
        startMock.mockRejectedValue(httpError(500, 'Could not start'))
        render(<SyncMapRebuildCard />)
        fireEvent.click(await screen.findByRole('button', { name: 'Dry run' }))
        expect(await screen.findByRole('alert')).toHaveTextContent('Could not start')
    })

    it('shows the server message when the confirmed start fails', async () => {
        startMock.mockRejectedValue(httpError(500, 'Could not start'))
        render(<SyncMapRebuildCard />)
        fireEvent.click(await screen.findByRole('button', { name: 'Rebuild 3 sync maps' }))
        fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Rebuild' }))
        expect(await screen.findByRole('alert')).toHaveTextContent('Could not start')
    })
})

describe('the Advanced pair-id filter', () => {
    async function openAdvanced() {
        const summary = await screen.findByText('Advanced')
        fireEvent.click(summary)
        return screen.getByLabelText('Only these pair ids')
    }

    it('sends valid ids as pair_ids and relabels the buttons', async () => {
        getStatusMock.mockResolvedValue(status({ outdated: 3, total: 9 }))
        render(<SyncMapRebuildCard />)
        const input = await openAdvanced()
        fireEvent.change(input, { target: { value: ' 4, 9 ,4' } })
        expect(screen.getByRole('button', { name: 'Rebuild selected' })).toBeEnabled()

        fireEvent.click(screen.getByRole('button', { name: 'Dry run' }))
        await waitFor(() => expect(startMock).toHaveBeenCalledWith({ dryRun: true, pairIds: [4, 9] }))
    })

    it('confirms and sends the selected ids for a real rebuild', async () => {
        getStatusMock.mockResolvedValue(status({ outdated: 3, total: 9 }))
        render(<SyncMapRebuildCard />)
        const input = await openAdvanced()
        fireEvent.change(input, { target: { value: '7' } })
        fireEvent.click(screen.getByRole('button', { name: 'Rebuild selected' }))
        const dialog = await screen.findByRole('dialog')
        expect(within(dialog).getByText(/Rebuild the selected sync maps\?/)).toBeInTheDocument()
        fireEvent.click(within(dialog).getByRole('button', { name: 'Rebuild' }))
        await waitFor(() => expect(startMock).toHaveBeenCalledWith({ dryRun: false, pairIds: [7] }))
    })

    it('disables the buttons and shows a hint for invalid input', async () => {
        getStatusMock.mockResolvedValue(status({ outdated: 3, total: 9 }))
        render(<SyncMapRebuildCard />)
        const input = await openAdvanced()
        for (const bad of ['abc', '1,,2', '0', '1.5', '-3']) {
            fireEvent.change(input, { target: { value: bad } })
            expect(screen.getByRole('button', { name: 'Dry run' })).toBeDisabled()
            expect(screen.getByText(/comma-separated pair ids/i)).toBeInTheDocument()
        }
        expect(startMock).not.toHaveBeenCalled()
    })

    it('returns to the whole set when the field is cleared', async () => {
        getStatusMock.mockResolvedValue(status({ outdated: 3, total: 9 }))
        render(<SyncMapRebuildCard />)
        const input = await openAdvanced()
        fireEvent.change(input, { target: { value: '5' } })
        fireEvent.change(input, { target: { value: '  ' } })
        expect(screen.getByRole('button', { name: 'Rebuild 3 sync maps' })).toBeEnabled()
        expect(screen.queryByText(/comma-separated pair ids/i)).not.toBeInTheDocument()
    })

    it('is available when everything is up to date, so one pair can be re-run on purpose', async () => {
        render(<SyncMapRebuildCard />)
        const input = await openAdvanced()
        expect(screen.queryByRole('button', { name: 'Dry run' })).not.toBeInTheDocument()
        fireEvent.change(input, { target: { value: '12' } })
        expect(screen.getByRole('button', { name: 'Rebuild selected' })).toBeEnabled()
        fireEvent.click(screen.getByRole('button', { name: 'Dry run' }))
        await waitFor(() => expect(startMock).toHaveBeenCalledWith({ dryRun: true, pairIds: [12] }))
    })

    it('keeps the buttons visible but disabled for invalid input when everything is up to date', async () => {
        render(<SyncMapRebuildCard />)
        const input = await openAdvanced()
        fireEvent.change(input, { target: { value: 'x' } })
        expect(screen.getByRole('button', { name: 'Dry run' })).toBeDisabled()
        expect(screen.getByRole('button', { name: 'Rebuild selected' })).toBeDisabled()
    })
})

describe('a running rebuild', () => {
    const running = (extra = {}) => status({
        outdated: 5, total: 9, running: true, to_process: 5, processed: 2, ...extra,
    })

    it('shows progress, a label, and a cancel button', async () => {
        getStatusMock.mockResolvedValue(running())
        render(<SyncMapRebuildCard />)
        expect(await screen.findByText('2 of 5')).toBeInTheDocument()
        const bar = screen.getByRole('progressbar')
        expect(bar).toHaveAttribute('max', '5')
        expect(bar).toHaveAttribute('value', '2')
        expect(screen.getByText(/Rebuilding sync maps/)).toBeInTheDocument()
        expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument()
        expect(screen.queryByRole('button', { name: 'Dry run' })).not.toBeInTheDocument()
    })

    it('labels a dry run as one', async () => {
        getStatusMock.mockResolvedValue(running({ dry_run: true }))
        render(<SyncMapRebuildCard />)
        expect(await screen.findByText(/Dry run in progress/)).toBeInTheDocument()
    })

    it('shows an empty bar when there is nothing to process yet', async () => {
        getStatusMock.mockResolvedValue(running({ to_process: 0, processed: 0 }))
        render(<SyncMapRebuildCard />)
        expect(await screen.findByText('0 of 0')).toBeInTheDocument()
        expect(screen.getByRole('progressbar')).toHaveAttribute('value', '0')
    })

    it('polls every two seconds and stops once the run is over', async () => {
        vi.useFakeTimers()
        getStatusMock
            .mockResolvedValueOnce(running())
            .mockResolvedValueOnce(running({ processed: 3 }))
            .mockResolvedValue(status({ outdated: 0, to_process: 5, processed: 5, succeeded: 5, finished_at: 'x' }))
        render(<SyncMapRebuildCard />)
        await act(async () => { await vi.advanceTimersByTimeAsync(0) })
        expect(getStatusMock).toHaveBeenCalledTimes(1)

        await act(async () => { await vi.advanceTimersByTimeAsync(1999) })
        expect(getStatusMock).toHaveBeenCalledTimes(1)
        await act(async () => { await vi.advanceTimersByTimeAsync(1) })
        expect(getStatusMock).toHaveBeenCalledTimes(2)
        expect(screen.getByText('3 of 5')).toBeInTheDocument()

        await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
        expect(getStatusMock).toHaveBeenCalledTimes(3)
        expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()

        await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
        expect(getStatusMock).toHaveBeenCalledTimes(3)
    })

    it('starts polling after a run is started from the card', async () => {
        vi.useFakeTimers()
        getStatusMock.mockResolvedValue(status({ outdated: 3, total: 9 }))
        render(<SyncMapRebuildCard />)
        await act(async () => { await vi.advanceTimersByTimeAsync(0) })
        fireEvent.click(screen.getByRole('button', { name: 'Dry run' }))
        await act(async () => { await vi.advanceTimersByTimeAsync(0) })
        expect(screen.getByRole('progressbar')).toBeInTheDocument()

        getStatusMock.mockResolvedValue(status({ outdated: 3, total: 9, running: true, to_process: 3, processed: 1 }))
        await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
        expect(screen.getByText('1 of 3')).toBeInTheDocument()
    })

    it('stops polling and updates nothing after unmount', async () => {
        vi.useFakeTimers()
        getStatusMock.mockResolvedValue(running())
        const { unmount } = render(<SyncMapRebuildCard />)
        await act(async () => { await vi.advanceTimersByTimeAsync(0) })
        expect(getStatusMock).toHaveBeenCalledTimes(1)
        unmount()
        await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
        expect(getStatusMock).toHaveBeenCalledTimes(1)
    })

    it('ignores a poll that resolves after unmount', async () => {
        vi.useFakeTimers()
        let resolvePoll
        getStatusMock
            .mockResolvedValueOnce(running())
            .mockImplementationOnce(() => new Promise((resolve) => { resolvePoll = resolve }))
        const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
        const { unmount } = render(<SyncMapRebuildCard />)
        await act(async () => { await vi.advanceTimersByTimeAsync(0) })
        await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
        unmount()
        await act(async () => { resolvePoll(status()) })
        expect(errorSpy).not.toHaveBeenCalled()
        errorSpy.mockRestore()
    })

    it('ignores a poll failure that lands after unmount', async () => {
        vi.useFakeTimers()
        let rejectPoll
        getStatusMock
            .mockResolvedValueOnce(running())
            .mockImplementationOnce(() => new Promise((_, reject) => { rejectPoll = reject }))
        const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
        const { unmount } = render(<SyncMapRebuildCard />)
        await act(async () => { await vi.advanceTimersByTimeAsync(0) })
        await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
        unmount()
        await act(async () => { rejectPoll(new Error('late')) })
        expect(errorSpy).not.toHaveBeenCalled()
        errorSpy.mockRestore()
    })

    it('shows a poll error and keeps polling', async () => {
        vi.useFakeTimers()
        getStatusMock
            .mockResolvedValueOnce(running())
            .mockRejectedValueOnce(new Error('Flaky'))
            .mockResolvedValue(running({ processed: 4 }))
        render(<SyncMapRebuildCard />)
        await act(async () => { await vi.advanceTimersByTimeAsync(0) })
        await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
        expect(screen.getByRole('alert')).toHaveTextContent('Flaky')
        await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
        expect(screen.getByText('4 of 5')).toBeInTheDocument()
        expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    })

    it('calls the cancel helper and shows the returned status', async () => {
        getStatusMock.mockResolvedValue(running())
        cancelMock.mockResolvedValue(status({ outdated: 3, total: 9, finished_at: 'x', cancelled: true, to_process: 5, processed: 2, succeeded: 2 }))
        render(<SyncMapRebuildCard />)
        fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }))
        await waitFor(() => expect(cancelMock).toHaveBeenCalledTimes(1))
        expect(await screen.findByText(/cancelled/)).toBeInTheDocument()
        expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
    })

    it('shows the error when cancelling fails', async () => {
        getStatusMock.mockResolvedValue(running())
        cancelMock.mockRejectedValue(httpError(500, 'No cancel for you'))
        render(<SyncMapRebuildCard />)
        fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }))
        expect(await screen.findByRole('alert')).toHaveTextContent('No cancel for you')
    })
})

describe('results of a run', () => {
    const results = [
        { pair_id: 11, outcome: 'rebuilt', detail: null, old_points: 100, new_points: 120, matched: 118, old_multiline_points: 7, bookmarks: 3, bookmarks_remapped: 2 },
        { pair_id: 12, outcome: 'failed', detail: 'No cached transcript', old_points: null, new_points: null, matched: null, old_multiline_points: null, bookmarks: null, bookmarks_remapped: null },
        { pair_id: 13, outcome: 'skipped', detail: 'Already current', old_points: 50, new_points: null, matched: null, old_multiline_points: 0, bookmarks: 0, bookmarks_remapped: 0 },
        { pair_id: 14, outcome: 'dry_run', detail: null, old_points: 80, new_points: 90, matched: 88, old_multiline_points: 4, bookmarks: 1, bookmarks_remapped: 1 },
    ]

    function rowFor(pairId) {
        return screen.getByText(String(pairId), { selector: 'td' }).closest('tr')
    }

    it('renders every outcome with "-" for missing numbers', async () => {
        getStatusMock.mockResolvedValue(status({
            outdated: 0, finished_at: 'x', to_process: 4, processed: 4, succeeded: 2, failed: 1, skipped: 1, results,
        }))
        render(<SyncMapRebuildCard />)
        await screen.findByRole('table')

        const headers = screen.getAllByRole('columnheader').map((h) => h.textContent)
        expect(headers).toEqual(['Pair', 'Outcome', 'Points (old → new)', 'Matched', 'Multi-line before', 'Positions moved', 'Detail'])

        const rebuilt = within(rowFor(11)).getAllByRole('cell').map((c) => c.textContent)
        expect(rebuilt).toEqual(['11', 'Rebuilt', '100 → 120', '118', '7', '2/3', '-'])

        const failed = within(rowFor(12)).getAllByRole('cell').map((c) => c.textContent)
        expect(failed).toEqual(['12', 'Failed', '-', '-', '-', '-', 'No cached transcript'])

        const skipped = within(rowFor(13)).getAllByRole('cell').map((c) => c.textContent)
        expect(skipped).toEqual(['13', 'Skipped', '50 → -', '-', '0', '0/0', 'Already current'])

        const dry = within(rowFor(14)).getAllByRole('cell').map((c) => c.textContent)
        expect(dry).toEqual(['14', 'Would rebuild', '80 → 90', '88', '4', '1/1', '-'])
    })

    it('summarises a real run', async () => {
        getStatusMock.mockResolvedValue(status({
            finished_at: 'x', to_process: 4, processed: 4, succeeded: 2, failed: 1, skipped: 1, results,
        }))
        render(<SyncMapRebuildCard />)
        expect(await screen.findByText('2 rebuilt, 1 failed, 1 skipped')).toBeInTheDocument()
    })

    it('summarises a dry run with "would rebuild"', async () => {
        getStatusMock.mockResolvedValue(status({
            dry_run: true, finished_at: 'x', to_process: 4, processed: 4, succeeded: 3, failed: 1, skipped: 0, results,
        }))
        render(<SyncMapRebuildCard />)
        expect(await screen.findByText('Dry run: 3 would rebuild, 1 failed, 0 skipped')).toBeInTheDocument()
    })

    it('mentions a cancelled run', async () => {
        getStatusMock.mockResolvedValue(status({
            finished_at: 'x', cancelled: true, to_process: 4, processed: 1, succeeded: 1, results: results.slice(0, 1),
        }))
        render(<SyncMapRebuildCard />)
        expect(await screen.findByText('1 rebuilt, 0 failed, 0 skipped, cancelled')).toBeInTheDocument()
    })

    it('keeps the results next to the buttons when maps are still outdated', async () => {
        getStatusMock.mockResolvedValue(status({
            outdated: 2, total: 9, finished_at: 'x', to_process: 1, processed: 1, failed: 1, results: results.slice(1, 2),
        }))
        render(<SyncMapRebuildCard />)
        expect(await screen.findByRole('table')).toBeInTheDocument()
        expect(screen.getByRole('button', { name: 'Rebuild 2 sync maps' })).toBeInTheDocument()
    })

    it('shows the up-to-date line above results when nothing is outdated any more', async () => {
        getStatusMock.mockResolvedValue(status({
            outdated: 0, finished_at: 'x', to_process: 1, processed: 1, succeeded: 1, results: results.slice(0, 1),
        }))
        render(<SyncMapRebuildCard />)
        expect(await screen.findByText('All sync maps are up to date.')).toBeInTheDocument()
        expect(screen.getByRole('table')).toBeInTheDocument()
    })
})
