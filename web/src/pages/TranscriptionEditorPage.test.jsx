import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import TranscriptionEditorPage from './TranscriptionEditorPage'

const { getSyncMapMock, getPairMock, getPairsMock, updateTranscriptionTextMock } = vi.hoisted(() => ({
    getSyncMapMock: vi.fn(),
    getPairMock: vi.fn(),
    getPairsMock: vi.fn(),
    updateTranscriptionTextMock: vi.fn(),
}))

vi.mock('../api', () => ({
    getSyncMap: getSyncMapMock,
    getPair: getPairMock,
    getPairs: getPairsMock,
    updateTranscriptionText: updateTranscriptionTextMock,
}))

beforeEach(() => {
    getSyncMapMock.mockReset()
    getPairMock.mockReset()
    getPairsMock.mockReset()
    updateTranscriptionTextMock.mockReset()
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

    it('renders the transcription points even when the pair lookup fails', async () => {
        // The pair is only a subheading. A 404 on it must not cost the editor
        // the sync points it exists to edit.
        getPairMock.mockRejectedValue(new Error('Pair not found'))
        getSyncMapMock.mockResolvedValue({
            sync_points: [{
                id: 1, audio_text: 'Ash fell from the sky.',
                audio_start_ms: 0, audio_end_ms: 2000,
                epub_chapter: 1, epub_sentence_index: 0,
            }],
        })

        renderAt('42')

        expect(await screen.findByDisplayValue('Ash fell from the sky.')).toBeInTheDocument()
        expect(screen.queryByText(/by Brandon Sanderson/)).not.toBeInTheDocument()
    })

    it('surfaces a sync-map failure as an error instead of an empty editor', async () => {
        getSyncMapMock.mockRejectedValue(new Error('Failed to fetch sync map'))

        renderAt('42')

        expect(await screen.findByText(/Failed to fetch sync map/)).toBeInTheDocument()
    })
})
