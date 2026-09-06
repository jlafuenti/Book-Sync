import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import ChapterEditor from './ChapterEditor'

// ChapterEditor was the largest untested behaviour-bearing file in web/src
// (0.7% of statements) when the coverage floor was ratcheted to 65/75/50/65.
//
// It is worth testing rather than excluding because the save path is not a
// pass-through: the component displays HH:MM:SS strings, edits them as text,
// and has to turn them back into seconds, re-sort by start time and recompute
// every end_time before it PUTs. A row typed out of order is the ordinary case
// — the whole reason chapters are hand-edited is that the embedded ones are
// wrong — and getting the conversion or the sort wrong writes a chapter list
// that plays back at the wrong offsets.

const { getAudiobookChaptersMock, updateAudiobookChaptersMock } = vi.hoisted(() => ({
    getAudiobookChaptersMock: vi.fn(),
    updateAudiobookChaptersMock: vi.fn(),
}))

vi.mock('../api', () => ({
    getAudiobookChapters: getAudiobookChaptersMock,
    updateAudiobookChapters: updateAudiobookChaptersMock,
}))

const CHAPTERS = [
    { id: 0, title: 'Prologue', start_time: 0, end_time: 61 },
    { id: 1, title: 'One', start_time: 61, end_time: 3725 },
    { id: 2, title: 'Two', start_time: 3725, end_time: 4000 },
]

beforeEach(() => {
    getAudiobookChaptersMock.mockReset().mockResolvedValue(
        CHAPTERS.map((c) => ({ ...c })),
    )
    updateAudiobookChaptersMock.mockReset().mockResolvedValue({})
})

afterEach(() => {
    vi.restoreAllMocks()
})

/** The start-time input on row `index` (rows are 0-based). */
function startTimeInput(index) {
    const row = screen.getAllByRole('row')[index + 1] // +1 for the header row
    return within(row).getAllByRole('textbox')[0]
}

function titleInput(index) {
    const row = screen.getAllByRole('row')[index + 1]
    return within(row).getAllByRole('textbox')[1]
}

async function renderLoaded(props = {}) {
    render(<ChapterEditor bookId={12} {...props} />)
    await screen.findByRole('table')
}

describe('ChapterEditor', () => {
    it('shows a loading line, then the chapters as HH:MM:SS', async () => {
        render(<ChapterEditor bookId={12} />)
        expect(screen.getByText('Loading chapters...')).toBeInTheDocument()

        await screen.findByRole('table')
        expect(getAudiobookChaptersMock).toHaveBeenCalledWith(12)
        expect(startTimeInput(0)).toHaveValue('00:00:00')
        expect(startTimeInput(1)).toHaveValue('00:01:01')
        // 3725s = 1h 2m 5s — the case that catches an h/m/s mix-up.
        expect(startTimeInput(2)).toHaveValue('01:02:05')
        expect(titleInput(1)).toHaveValue('One')
    })

    it('reloads when it is pointed at a different audiobook', async () => {
        const { rerender } = render(<ChapterEditor bookId={12} />)
        await screen.findByRole('table')

        rerender(<ChapterEditor bookId={99} />)

        await waitFor(() => expect(getAudiobookChaptersMock).toHaveBeenCalledTimes(2))
        expect(getAudiobookChaptersMock).toHaveBeenLastCalledWith(99)
    })

    it('says so when the audiobook carries no chapters', async () => {
        getAudiobookChaptersMock.mockResolvedValue([])
        render(<ChapterEditor bookId={12} />)

        expect(await screen.findByText('No chapters found in this audiobook.')).toBeInTheDocument()
        expect(screen.queryByRole('table')).not.toBeInTheDocument()
        // Nothing to remove, so the destructive button is off.
        expect(screen.getByRole('button', { name: /Remove All/ })).toBeDisabled()
    })

    it('surfaces a load failure instead of an empty editor', async () => {
        getAudiobookChaptersMock.mockRejectedValue(new Error('chapters unavailable'))
        render(<ChapterEditor bookId={12} />)

        expect(await screen.findByText('chapters unavailable')).toBeInTheDocument()
    })

    it('adds a chapter five minutes after the last one', async () => {
        await renderLoaded()

        fireEvent.click(screen.getByRole('button', { name: '+ Add Chapter' }))

        // Last start was 01:02:05; the new row is +5:00 from it.
        expect(startTimeInput(3)).toHaveValue('01:07:05')
        expect(titleInput(3)).toHaveValue('Chapter 4')
    })

    it('starts the first added chapter at zero plus five minutes', async () => {
        getAudiobookChaptersMock.mockResolvedValue([])
        render(<ChapterEditor bookId={12} />)
        await screen.findByText('No chapters found in this audiobook.')

        fireEvent.click(screen.getByRole('button', { name: '+ Add Chapter' }))

        expect(await screen.findByRole('table')).toBeInTheDocument()
        expect(startTimeInput(0)).toHaveValue('00:05:00')
    })

    it('removes one row without disturbing the others', async () => {
        await renderLoaded()

        fireEvent.click(within(screen.getAllByRole('row')[2]).getByRole('button', { name: 'X' }))

        expect(screen.getAllByRole('row')).toHaveLength(3) // header + 2
        expect(titleInput(0)).toHaveValue('Prologue')
        expect(titleInput(1)).toHaveValue('Two')
    })

    it('asks before removing every chapter, and keeps them if the answer is no', async () => {
        const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
        await renderLoaded()

        fireEvent.click(screen.getByRole('button', { name: /Remove All/ }))

        expect(confirm).toHaveBeenCalled()
        expect(screen.getAllByRole('row')).toHaveLength(4)

        confirm.mockReturnValue(true)
        fireEvent.click(screen.getByRole('button', { name: /Remove All/ }))
        expect(await screen.findByText('No chapters found in this audiobook.')).toBeInTheDocument()
    })

    it('saves edited times as seconds, sorted, with end times chained', async () => {
        await renderLoaded()

        // Type a row out of order — the ordinary case, since the reason to hand-
        // edit chapters is that the embedded ones are wrong.
        fireEvent.change(startTimeInput(2), { target: { value: '00:00:30' } })
        fireEvent.change(titleInput(2), { target: { value: 'Actually second' } })

        fireEvent.click(screen.getByRole('button', { name: /Save Chapters/ }))

        await waitFor(() => expect(updateAudiobookChaptersMock).toHaveBeenCalled())
        const [bookId, payload] = updateAudiobookChaptersMock.mock.calls[0]
        expect(bookId).toBe(12)
        expect(payload.map((c) => [c.title, c.start_time])).toEqual([
            ['Prologue', 0],
            ['Actually second', 30],
            ['One', 61],
        ])
        // Each end_time is the next chapter's start...
        expect(payload[0].end_time).toBe(30)
        expect(payload[1].end_time).toBe(61)
        // ...and the last one gets ten hours, because the file duration is not
        // known here.
        expect(payload[2].end_time).toBe(61 + 36000)
    })

    it('accepts MM:SS and a bare seconds count as well as HH:MM:SS', async () => {
        await renderLoaded()

        fireEvent.change(startTimeInput(1), { target: { value: '2:30' } })
        fireEvent.change(startTimeInput(2), { target: { value: '900' } })

        fireEvent.click(screen.getByRole('button', { name: /Save Chapters/ }))

        await waitFor(() => expect(updateAudiobookChaptersMock).toHaveBeenCalled())
        const payload = updateAudiobookChaptersMock.mock.calls[0][1]
        expect(payload.map((c) => c.start_time)).toEqual([0, 150, 900])
    })

    it('treats an emptied time field as the start of the book', async () => {
        await renderLoaded()

        fireEvent.change(startTimeInput(1), { target: { value: '' } })
        fireEvent.click(screen.getByRole('button', { name: /Save Chapters/ }))

        await waitFor(() => expect(updateAudiobookChaptersMock).toHaveBeenCalled())
        const payload = updateAudiobookChaptersMock.mock.calls[0][1]
        expect(payload.map((c) => c.start_time)).toEqual([0, 0, 3725])
    })

    it('confirms the save and re-reads the chapters the server kept', async () => {
        await renderLoaded()

        fireEvent.click(screen.getByRole('button', { name: /Save Chapters/ }))

        expect(await screen.findByText('Chapters saved successfully!')).toBeInTheDocument()
        // The re-read is what makes the editor show the server's version rather
        // than the local one it just sent.
        expect(getAudiobookChaptersMock).toHaveBeenCalledTimes(2)
    })

    it('reports a failed save and leaves the edits on screen', async () => {
        updateAudiobookChaptersMock.mockRejectedValue(new Error('write denied'))
        await renderLoaded()

        fireEvent.change(titleInput(0), { target: { value: 'Renamed' } })
        fireEvent.click(screen.getByRole('button', { name: /Save Chapters/ }))

        expect(await screen.findByText('write denied')).toBeInTheDocument()
        expect(screen.queryByText('Chapters saved successfully!')).not.toBeInTheDocument()
        expect(titleInput(0)).toHaveValue('Renamed')
        // No reload on failure — the unsaved edit must not be thrown away.
        expect(getAudiobookChaptersMock).toHaveBeenCalledTimes(1)
    })

    it('disables the save button while the write is in flight', async () => {
        let release
        updateAudiobookChaptersMock.mockReturnValue(new Promise((r) => { release = r }))
        await renderLoaded()

        fireEvent.click(screen.getByRole('button', { name: /Save Chapters/ }))

        const button = await screen.findByRole('button', { name: 'Saving...' })
        expect(button).toBeDisabled()

        release({})
        await waitFor(() => expect(screen.getByRole('button', { name: /Save Chapters/ })).toBeEnabled())
    })
})
