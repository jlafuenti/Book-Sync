import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import TranscriptionEditorPage from './TranscriptionEditorPage'

const { getSyncMapMock, getPairMock, getPairsMock, realignPairMock, authRef } = vi.hoisted(() => ({
    getSyncMapMock: vi.fn(),
    getPairMock: vi.fn(),
    getPairsMock: vi.fn(),
    realignPairMock: vi.fn(),
    authRef: { editor: true },
}))

vi.mock('../api', () => ({
    getSyncMap: getSyncMapMock,
    getPair: getPairMock,
    getPairs: getPairsMock,
    realignPair: realignPairMock,
}))

vi.mock('../contexts/AuthContext', () => ({
    useAuth: () => ({ hasMinRole: () => authRef.editor }),
}))

const POINTS = [
    {
        id: 1, epub_text_preview: 'Ash fell from the sky.', audio_text: 'ash fell from the sky',
        audio_start_ms: 0, audio_end_ms: 2000, epub_chapter: 1, epub_sentence_index: 0, confidence: 0.92,
    },
    {
        id: 2, epub_text_preview: 'The mists came at night.', audio_text: null,
        audio_start_ms: 2000, audio_end_ms: 65000, epub_chapter: 1, epub_sentence_index: 1, confidence: 0,
    },
]

beforeEach(() => {
    authRef.editor = true
    getSyncMapMock.mockReset()
    getPairMock.mockReset()
    getPairsMock.mockReset()
    realignPairMock.mockReset()
    getSyncMapMock.mockResolvedValue({ sync_points: [] })
    getPairMock.mockResolvedValue({
        id: 42,
        ebook: { title: 'The Final Empire', author: 'Brandon Sanderson' },
    })
})

function renderAt(pairId = '42') {
    return render(
        <MemoryRouter initialEntries={[`/transcription/edit/${pairId}`]}>
            <Routes>
                <Route path="/transcription/edit/:pairId" element={<TranscriptionEditorPage />} />
            </Routes>
        </MemoryRouter>,
    )
}

// Issue #277: this page used to call `getPairs()`, which walks every page of
// /api/library/pairs at 500 rows a page, purely to `.find()` the one pair whose
// title it puts in the subheading. It now asks the server for that one pair.
describe('TranscriptionEditorPage pair lookup (issue #277)', () => {
    it('fetches exactly the routed pair and never lists the library', async () => {
        renderAt('42')

        expect(await screen.findByText(/The Final Empire/)).toBeInTheDocument()
        expect(getPairMock).toHaveBeenCalledTimes(1)
        expect(getPairMock).toHaveBeenCalledWith('42')
        expect(getPairsMock).not.toHaveBeenCalled()
    })

    it('renders the alignment even when the pair lookup fails', async () => {
        // The pair is only a subheading. A 404 on it must not cost the page
        // the sync points it exists to show.
        getPairMock.mockRejectedValue(new Error('Pair not found'))
        getSyncMapMock.mockResolvedValue({ sync_points: POINTS })

        renderAt('42')

        expect(await screen.findByText('Ash fell from the sky.')).toBeInTheDocument()
        expect(screen.queryByText(/by Brandon Sanderson/)).not.toBeInTheDocument()
    })

    it('surfaces a sync-map failure as an error instead of an empty page', async () => {
        getSyncMapMock.mockRejectedValue(new Error('Failed to fetch sync map'))

        renderAt('42')

        expect(await screen.findByText(/Failed to fetch sync map/)).toBeInTheDocument()
    })
})

// Issue #713: edits here never reached alignment (re-align rebuilds from the
// cached transcript), so the page is a read-only view with a Re-align button.
describe('TranscriptionEditorPage is a read-only alignment view (issue #713)', () => {
    it('shows each ebook sentence beside what was heard, with no text to edit', async () => {
        getSyncMapMock.mockResolvedValue({ sync_points: POINTS })
        const { container } = renderAt('42')

        expect(await screen.findByText('Ash fell from the sky.')).toBeInTheDocument()
        expect(screen.getByText('ash fell from the sky')).toBeInTheDocument()
        expect(screen.getByText('0:02 – 1:05')).toBeInTheDocument()
        expect(container.querySelector('textarea')).toBeNull()
        expect(screen.queryByRole('button', { name: /save/i })).toBeNull()
        expect(screen.queryByRole('button', { name: /replace/i })).toBeNull()
    })

    it('marks sentences filled in between matches and counts both kinds', async () => {
        getSyncMapMock.mockResolvedValue({ sync_points: POINTS })
        renderAt('42')

        expect(await screen.findByText('1 matched, 1 filled in between matches')).toBeInTheDocument()
        expect(screen.getByText('Filled in')).toBeInTheDocument()
        expect(screen.getByText('No transcript text in this range')).toBeInTheDocument()
    })

    it('filters the sentences by a search', async () => {
        getSyncMapMock.mockResolvedValue({ sync_points: POINTS })
        renderAt('42')
        await screen.findByText('Ash fell from the sky.')

        fireEvent.change(screen.getByLabelText('Search sentences'), { target: { value: 'MISTS' } })

        expect(screen.queryByText('Ash fell from the sky.')).toBeNull()
        expect(screen.getByText('The mists came at night.')).toBeInTheDocument()
        expect(screen.getByText('1 of 2 sentences')).toBeInTheDocument()
    })

    it('re-aligns from the saved transcript and reloads the view', async () => {
        getSyncMapMock.mockResolvedValue({ sync_points: POINTS })
        realignPairMock.mockResolvedValue({ status: 'ok', points: 2, matched: 2, interpolated: 0 })
        renderAt('42')
        await screen.findByText('Ash fell from the sky.')

        fireEvent.click(screen.getByRole('button', { name: 'Re-align' }))

        await waitFor(() => expect(realignPairMock).toHaveBeenCalledWith('42'))
        expect(await screen.findByText(/Re-aligned: 2 sentences, 2 matched./)).toBeInTheDocument()
        expect(getSyncMapMock).toHaveBeenCalledTimes(2)
    })

    it('shows why a re-align failed', async () => {
        getSyncMapMock.mockResolvedValue({ sync_points: POINTS })
        realignPairMock.mockRejectedValue(new Error('No cached transcript for this pair — run full transcription instead.'))
        renderAt('42')
        await screen.findByText('Ash fell from the sky.')

        fireEvent.click(screen.getByRole('button', { name: 'Re-align' }))

        expect(await screen.findByText(/No cached transcript for this pair/)).toBeInTheDocument()
    })

    it('offers no Re-align below the editor role', async () => {
        authRef.editor = false
        getSyncMapMock.mockResolvedValue({ sync_points: POINTS })
        renderAt('42')
        await screen.findByText('Ash fell from the sky.')

        expect(screen.queryByRole('button', { name: 'Re-align' })).toBeNull()
    })
})
