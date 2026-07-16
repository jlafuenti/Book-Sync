import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import BookDetailPage from './BookDetailPage'

// Isolate BookDetailPage from its heavier children/deps so this test only
// exercises the enrich-from-ABS toast logic.
const {
    getAudiobookMock, getSettingsMock, enrichAudiobookFromAbsMock, getProgressMock, getBookmarkMock,
} = vi.hoisted(() => ({
    getAudiobookMock: vi.fn(),
    getSettingsMock: vi.fn(),
    enrichAudiobookFromAbsMock: vi.fn(),
    getProgressMock: vi.fn(),
    getBookmarkMock: vi.fn(),
}))

vi.mock('../api', () => ({
    getEbook: vi.fn(),
    getAudiobook: getAudiobookMock,
    updateEbookMetadata: vi.fn(),
    updateAudiobookMetadata: vi.fn(),
    rescanBook: vi.fn(),
    getSettings: getSettingsMock,
    enrichAudiobookFromAbs: enrichAudiobookFromAbsMock,
    getProgress: getProgressMock,
    updateProgress: vi.fn(),
    getBookmark: getBookmarkMock,
    updateBookmark: vi.fn(),
}))

vi.mock('react-markdown', () => ({ default: ({ children }) => <div>{children}</div> }))
vi.mock('../components/EnhancedMetadataModal', () => ({ default: () => null }))
vi.mock('../components/EbookReader', () => ({ default: () => null }))
vi.mock('../components/AudioPlayer', () => ({ AudioPlayerView: () => null }))
vi.mock('../components/CoverImg', () => ({ default: () => null }))
vi.mock('../contexts/AuthContext', () => ({ useAuth: () => ({ hasMinRole: () => true }) }))
vi.mock('../contexts/AudioPlayerContext', () => ({ useAudioPlayer: () => ({ play: vi.fn() }) }))

function renderPage() {
    return render(
        <MemoryRouter initialEntries={['/book/audiobook/1538']}>
            <Routes>
                <Route path="/book/:type/:id" element={<BookDetailPage />} />
            </Routes>
        </MemoryRouter>
    )
}

beforeEach(() => {
    getAudiobookMock.mockReset().mockResolvedValue({
        id: 1538, title: 'Antiagon Fire', author: 'L. E. Modesitt Jr', cover_path: null,
    })
    getSettingsMock.mockReset().mockResolvedValue({ abs_enabled: true })
    enrichAudiobookFromAbsMock.mockReset()
    getProgressMock.mockReset().mockResolvedValue(null)
    getBookmarkMock.mockReset().mockResolvedValue(null)
})

describe('BookDetailPage enrich from ABS', () => {
    it('shows an error toast when the tag write to the file failed', async () => {
        enrichAudiobookFromAbsMock.mockResolvedValue({
            status: 'tag_write_failed',
            message: "Metadata updated in the library, but writing tags to the file failed: 'utf-8' codec can't decode byte 0xc4 in position 27: invalid continuation byte",
            book: { id: 1538, title: 'Antiagon Fire' },
            tag_write_error: "'utf-8' codec can't decode byte 0xc4 in position 27: invalid continuation byte",
        })
        renderPage()

        fireEvent.click(await screen.findByRole('button', { name: /Enrich from ABS/ }))

        const toast = await screen.findByText(/writing tags to the file failed/)
        expect(toast.getAttribute('style')).toContain('var(--error)')
    })

    it('shows a success toast when enrichment and tag write both succeed', async () => {
        enrichAudiobookFromAbsMock.mockResolvedValue({
            status: 'enriched',
            message: 'Metadata enriched from Audiobookshelf and written back to file.',
            book: { id: 1538, title: 'Antiagon Fire' },
            tag_write_error: null,
        })
        renderPage()

        fireEvent.click(await screen.findByRole('button', { name: /Enrich from ABS/ }))

        const toast = await screen.findByText(/written back to file/)
        expect(toast.getAttribute('style')).toContain('var(--success)')
    })
})
