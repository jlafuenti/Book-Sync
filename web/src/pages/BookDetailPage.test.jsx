import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import BookDetailPage from './BookDetailPage'

// Isolate BookDetailPage from its heavier children/deps so this test only
// exercises the enrich-from-ABS toast logic.
const {
    getAudiobookMock, getSettingsMock, enrichAudiobookFromAbsMock, getProgressMock, getPositionMock,
    updatePositionMock, resetPairProgressMock, resetPositionMock, getDeviceIdMock, getDeviceNameMock,
} = vi.hoisted(() => ({
    getAudiobookMock: vi.fn(),
    getSettingsMock: vi.fn(),
    enrichAudiobookFromAbsMock: vi.fn(),
    getProgressMock: vi.fn(),
    getPositionMock: vi.fn(),
    updatePositionMock: vi.fn(),
    resetPairProgressMock: vi.fn(),
    resetPositionMock: vi.fn(),
    getDeviceIdMock: vi.fn(() => 'device-abc'),
    getDeviceNameMock: vi.fn(() => 'Web · Chrome'),
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
    updatePosition: updatePositionMock,
    resetPairProgress: resetPairProgressMock,
    resetPosition: resetPositionMock,
    getPosition: getPositionMock,
    getDeviceId: getDeviceIdMock,
    getDeviceName: getDeviceNameMock,
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
    getPositionMock.mockReset().mockResolvedValue(null)
    updatePositionMock.mockReset().mockResolvedValue({})
    resetPairProgressMock.mockReset().mockResolvedValue({})
    resetPositionMock.mockReset().mockResolvedValue({ status: 'ok' })
    getDeviceIdMock.mockReset().mockReturnValue('device-abc')
    getDeviceNameMock.mockReset().mockReturnValue('Web · Chrome')
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

describe('BookDetailPage progress actions (issue #54 device attribution)', () => {
    it('sends device_id, device_name, and captured_at when marking complete', async () => {
        getProgressMock.mockResolvedValue({ is_completed: false, audio_position_ms: 1000 })
        renderPage()

        fireEvent.click(await screen.findByRole('button', { name: /Mark Complete/ }))

        await waitFor(() => expect(updatePositionMock).toHaveBeenCalled())
        expect(updatePositionMock).toHaveBeenCalledWith('audiobook', '1538', expect.objectContaining({
            is_completed: true,
            device_id: 'device-abc',
            device_name: 'Web · Chrome',
            captured_at: expect.any(String),
        }))
    })

    it('resets a standalone book with the scoped DELETE, not a zero-write', async () => {
        // The zero-write this replaced left the canonical record in place, so
        // the next save resurrected the position (issue #102 / issue #6).
        getProgressMock.mockResolvedValue({ is_completed: false, audio_position_ms: 1000 })
        renderPage()

        fireEvent.click(await screen.findByRole('button', { name: /Reset Progress/ }))

        await waitFor(() => expect(resetPositionMock).toHaveBeenCalledWith('audiobook', '1538'))
        expect(updatePositionMock).not.toHaveBeenCalled()
        expect(resetPairProgressMock).not.toHaveBeenCalled()
    })

    it('calls the pair-level DELETE instead of zero-writing when the book is paired', async () => {
        // A paired book must use the pair-scoped DELETE, which also sweeps
        // the standalone rows for its own media — resetting only this media's
        // scope would leave the pair's record to resurrect the position.
        getAudiobookMock.mockResolvedValue({
            id: 1538, title: 'Antiagon Fire', author: 'L. E. Modesitt Jr', cover_path: null, pair_id: 77,
        })
        getProgressMock.mockResolvedValue({ is_completed: false, audio_position_ms: 1000 })
        renderPage()

        fireEvent.click(await screen.findByRole('button', { name: /Reset Progress/ }))

        await waitFor(() => expect(resetPairProgressMock).toHaveBeenCalledWith(77))
        expect(resetPositionMock).not.toHaveBeenCalled()
        expect(updatePositionMock).not.toHaveBeenCalled()
    })
})
