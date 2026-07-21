import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import ContinuePage from './ContinuePage'

const {
    getAllProgressMock, getEbooksMock, getAudiobooksMock, getPairsMock,
    updateProgressMock, getProgressMock, getBookmarkMock, updateBookmarkMock,
    getAccessTokenMock, getDeviceIdMock, getDeviceNameMock,
} = vi.hoisted(() => ({
    getAllProgressMock: vi.fn(),
    getEbooksMock: vi.fn(),
    getAudiobooksMock: vi.fn(),
    getPairsMock: vi.fn(),
    updateProgressMock: vi.fn(),
    getProgressMock: vi.fn(),
    getBookmarkMock: vi.fn(),
    updateBookmarkMock: vi.fn(),
    getAccessTokenMock: vi.fn(() => 'token'),
    getDeviceIdMock: vi.fn(() => 'device-abc'),
    getDeviceNameMock: vi.fn(() => 'Web · Chrome'),
}))

vi.mock('../api', () => ({
    getAllProgress: getAllProgressMock,
    getEbooks: getEbooksMock,
    getAudiobooks: getAudiobooksMock,
    getPairs: getPairsMock,
    updateProgress: updateProgressMock,
    getProgress: getProgressMock,
    getBookmark: getBookmarkMock,
    updateBookmark: updateBookmarkMock,
    getAccessToken: getAccessTokenMock,
    getDeviceId: getDeviceIdMock,
    getDeviceName: getDeviceNameMock,
}))

// Isolate ContinuePage from its heavier children -- EbookReader pulls in
// epubjs and isn't relevant to the device-attribution logic under test.
vi.mock('../contexts/AudioPlayerContext', () => ({
    useAudioPlayer: () => ({
        play: vi.fn(),
        pause: vi.fn(),
        currentAudiobook: null,
        pairedEbookId: null,
        currentTime: 0,
    }),
}))
vi.mock('../components/EbookReader', () => ({ default: () => null }))
vi.mock('../components/AudioPlayer', () => ({ AudioPlayerView: () => null }))

beforeEach(() => {
    getAllProgressMock.mockReset().mockResolvedValue([])
    getEbooksMock.mockReset().mockResolvedValue([])
    getAudiobooksMock.mockReset().mockResolvedValue([])
    getPairsMock.mockReset().mockResolvedValue([])
    updateProgressMock.mockReset().mockResolvedValue({})
    getProgressMock.mockReset().mockResolvedValue(null)
    getBookmarkMock.mockReset().mockResolvedValue(null)
    updateBookmarkMock.mockReset().mockResolvedValue({})
    getAccessTokenMock.mockReset().mockReturnValue('token')
    getDeviceIdMock.mockReset().mockReturnValue('device-abc')
    getDeviceNameMock.mockReset().mockReturnValue('Web · Chrome')
})

describe('ContinuePage device attribution (issue #54)', () => {
    // Library fixture: one paired item (ebook 10 + audiobook 20, pair 100),
    // one standalone ebook (11), one standalone audiobook (21).
    function setupLibrary() {
        getEbooksMock.mockResolvedValue([
            { id: 10, title: 'Pair Ebook', cover_path: null },
            { id: 11, title: 'Ebook A', cover_path: null },
        ])
        getAudiobooksMock.mockResolvedValue([
            { id: 20, title: 'Pair Audiobook', cover_path: null, duration_seconds: 3600 },
            { id: 21, title: 'Audiobook A', cover_path: null, duration_seconds: 3600 },
        ])
        getPairsMock.mockResolvedValue([
            { id: 100, ebook: { id: 10 }, audiobook: { id: 20 } },
        ])
        getAllProgressMock.mockResolvedValue([
            { id: 1, media_type: 'ebook', ebook_id: 10, epub_progress_percent: 20, epub_chapter: 0, book_pair_id: 100, is_completed: false, updated_at: '2024-01-01T00:00:00Z' },
            { id: 2, media_type: 'ebook', ebook_id: 11, epub_progress_percent: 40, epub_chapter: 0, book_pair_id: null, is_completed: false, updated_at: '2024-01-02T00:00:00Z' },
            { id: 3, media_type: 'audiobook', audiobook_id: 21, audio_position_ms: 700, book_pair_id: null, is_completed: false, updated_at: '2024-01-03T00:00:00Z' },
        ])
    }

    function renderPage() {
        return render(<MemoryRouter><ContinuePage /></MemoryRouter>)
    }

    async function openMenu(cardTitle) {
        const titleEl = await screen.findByText(cardTitle)
        const card = titleEl.closest('.continue-card')
        fireEvent.click(card.querySelector('.continue-card-menu'))
        return card
    }

    it('sends device_id/device_name/captured_at for both legs of a paired Mark Complete', async () => {
        setupLibrary()
        renderPage()

        const card = await openMenu('Pair Ebook')
        fireEvent.click(within(card).getByText('Mark Complete'))

        await waitFor(() => expect(updateProgressMock).toHaveBeenCalledWith('ebook', 10, expect.objectContaining({
            is_completed: true, device_id: 'device-abc', device_name: 'Web · Chrome', captured_at: expect.any(String),
        })))
        expect(updateProgressMock).toHaveBeenCalledWith('audiobook', 20, expect.objectContaining({
            is_completed: true, device_id: 'device-abc', device_name: 'Web · Chrome', captured_at: expect.any(String),
        }))
    })

    it('sends device fields for a standalone (non-pair) Mark Complete', async () => {
        setupLibrary()
        renderPage()

        const card = await openMenu('Ebook A')
        fireEvent.click(within(card).getByText('Mark Complete'))

        await waitFor(() => expect(updateProgressMock).toHaveBeenCalledWith('ebook', 11, expect.objectContaining({
            is_completed: true, device_id: 'device-abc', device_name: 'Web · Chrome', captured_at: expect.any(String),
        })))
    })

    it('sends device fields for both legs of a paired Reset Progress', async () => {
        setupLibrary()
        renderPage()

        const card = await openMenu('Pair Ebook')
        fireEvent.click(within(card).getByText('Reset Progress'))

        await waitFor(() => expect(updateProgressMock).toHaveBeenCalledWith('ebook', 10, expect.objectContaining({
            is_completed: false, device_id: 'device-abc', device_name: 'Web · Chrome', captured_at: expect.any(String),
            epub_progress_percent: 0, epub_cfi: '', epub_chapter: 0,
        })))
        expect(updateProgressMock).toHaveBeenCalledWith('audiobook', 20, expect.objectContaining({
            is_completed: false, device_id: 'device-abc', device_name: 'Web · Chrome', captured_at: expect.any(String),
            audio_position_ms: 0,
        }))
    })

    it('sends device fields for a standalone ebook Reset Progress', async () => {
        setupLibrary()
        renderPage()

        const card = await openMenu('Ebook A')
        fireEvent.click(within(card).getByText('Reset Progress'))

        await waitFor(() => expect(updateProgressMock).toHaveBeenCalledWith('ebook', 11, expect.objectContaining({
            is_completed: false, device_id: 'device-abc', device_name: 'Web · Chrome', captured_at: expect.any(String),
            epub_progress_percent: 0, epub_cfi: '', epub_chapter: 0,
        })))
    })

    it('sends device fields for a standalone audiobook Reset Progress', async () => {
        setupLibrary()
        renderPage()

        const card = await openMenu('Audiobook A')
        fireEvent.click(within(card).getByText('Reset Progress'))

        await waitFor(() => expect(updateProgressMock).toHaveBeenCalledWith('audiobook', 21, expect.objectContaining({
            is_completed: false, device_id: 'device-abc', device_name: 'Web · Chrome', captured_at: expect.any(String),
            audio_position_ms: 0,
        })))
    })
})
