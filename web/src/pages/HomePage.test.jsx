import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import HomePage, { BookCard } from './HomePage'

const {
    getAllProgressMock, getEbooksMock, getAudiobooksMock, getPairsMock, getTranscriptionQueueMock,
    updatePositionMock, resetPairProgressMock, resetPositionMock, getProgressMock, getPositionMock,
    getDeviceIdMock, getDeviceNameMock, coverSrcMock,
} = vi.hoisted(() => ({
    getAllProgressMock: vi.fn(),
    getEbooksMock: vi.fn(),
    getAudiobooksMock: vi.fn(),
    getPairsMock: vi.fn(),
    getTranscriptionQueueMock: vi.fn(),
    updatePositionMock: vi.fn(),
    resetPairProgressMock: vi.fn(),
    resetPositionMock: vi.fn(),
    getProgressMock: vi.fn(),
    getPositionMock: vi.fn(),
    getDeviceIdMock: vi.fn(() => 'device-abc'),
    getDeviceNameMock: vi.fn(() => 'Web · Chrome'),
    coverSrcMock: vi.fn(),
}))

vi.mock('../api', () => ({
    getAllProgress: getAllProgressMock,
    getEbooks: getEbooksMock,
    getAudiobooks: getAudiobooksMock,
    getPairs: getPairsMock,
    getTranscriptionQueue: getTranscriptionQueueMock,
    updatePosition: updatePositionMock,
    resetPairProgress: resetPairProgressMock,
    resetPosition: resetPositionMock,
    getProgress: getProgressMock,
    getPosition: getPositionMock,
    getDeviceId: getDeviceIdMock,
    getDeviceName: getDeviceNameMock,
    coverSrc: coverSrcMock,
}))

// Isolate HomePage from its heavier children -- EbookReader pulls in epubjs,
// AudioPlayer/CoverImg aren't relevant to the device-attribution logic under test.
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
vi.mock('../components/CoverImg', () => ({ default: () => null }))

beforeEach(() => {
    coverSrcMock.mockReset()
    getAllProgressMock.mockReset().mockResolvedValue([])
    getEbooksMock.mockReset().mockResolvedValue([])
    getAudiobooksMock.mockReset().mockResolvedValue([])
    getPairsMock.mockReset().mockResolvedValue([])
    getTranscriptionQueueMock.mockReset().mockResolvedValue([])
    updatePositionMock.mockReset().mockResolvedValue({})
    resetPairProgressMock.mockReset().mockResolvedValue({})
    getProgressMock.mockReset().mockResolvedValue(null)
    getPositionMock.mockReset().mockResolvedValue(null)
    resetPositionMock.mockReset().mockResolvedValue({ status: 'ok' })
    getDeviceIdMock.mockReset().mockReturnValue('device-abc')
    getDeviceNameMock.mockReset().mockReturnValue('Web · Chrome')

    // jsdom has no ResizeObserver; HomePage's Carousel observes its scroll
    // container to toggle arrow visibility.
    global.ResizeObserver = class {
        observe() {}
        unobserve() {}
        disconnect() {}
    }
})

describe('HomePage BookCard', () => {
    it('renders the resolved cover image once coverSrc() resolves', async () => {
        coverSrcMock.mockResolvedValue('/api/files/covers/a.jpg?token=tok')
        render(<BookCard book={{ title: 'A Book', cover_path: '/api/files/covers/a.jpg' }} onPrimary={() => {}} />)

        const img = await screen.findByAltText('A Book')
        expect(img).toHaveAttribute('src', '/api/files/covers/a.jpg?token=tok')
    })

    it('shows a placeholder when there is no cover_path', () => {
        render(<BookCard book={{ title: 'A Book', cover_path: null }} onPrimary={() => {}} />)
        expect(screen.queryByRole('img')).not.toBeInTheDocument()
        expect(coverSrcMock).not.toHaveBeenCalled()
    })
})

describe('HomePage Continue Reading device attribution (issue #54)', () => {
    // Library fixture: one paired item (ebook 10 + audiobook 20, pair 100),
    // one standalone ebook (11), one standalone audiobook (21). All three
    // books also appear (unpaired) in "Recently Added", which renders the
    // same titles without a menu -- tests disambiguate via the `.continue-size`
    // class that only the Continue Reading BookCard instances carry.
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

    function renderHome() {
        return render(<MemoryRouter><HomePage /></MemoryRouter>)
    }

    async function openMenu(cardTitle) {
        const matches = await screen.findAllByText(cardTitle)
        const card = matches.map((el) => el.closest('.continue-size')).find(Boolean)
        fireEvent.click(within(card).getByTitle('More options'))
        return card
    }

    it('marks a pair complete with one canonical write carrying the device fields', async () => {
        setupLibrary()
        renderHome()

        const card = await openMenu('Pair Ebook')
        fireEvent.click(within(card).getByText('Mark Complete'))

        // ONE write for the pair: both halves share a single canonical record,
        // so the two per-media writes this replaced could be adjudicated
        // separately and leave the book half-complete.
        await waitFor(() => expect(updatePositionMock).toHaveBeenCalledWith('pair', 100, expect.objectContaining({
            is_completed: true, device_id: 'device-abc', device_name: 'Web · Chrome', captured_at: expect.any(String),
        })))
        expect(updatePositionMock).toHaveBeenCalledTimes(1)
    })

    it('sends device fields for a standalone (non-pair) Mark Complete', async () => {
        setupLibrary()
        renderHome()

        const card = await openMenu('Ebook A')
        fireEvent.click(within(card).getByText('Mark Complete'))

        await waitFor(() => expect(updatePositionMock).toHaveBeenCalledWith('ebook', 11, expect.objectContaining({
            is_completed: true, device_id: 'device-abc', device_name: 'Web · Chrome', captured_at: expect.any(String),
        })))
    })

    it('resets a standalone ebook with the scoped DELETE, not a zero-write', async () => {
        setupLibrary()
        renderHome()

        const card = await openMenu('Ebook A')
        fireEvent.click(within(card).getByText('Reset Progress'))

        await waitFor(() => expect(resetPositionMock).toHaveBeenCalledWith('ebook', 11))
        expect(updatePositionMock).not.toHaveBeenCalled()
    })

    it('resets a standalone audiobook with the scoped DELETE, not a zero-write', async () => {
        setupLibrary()
        renderHome()

        const card = await openMenu('Audiobook A')
        fireEvent.click(within(card).getByText('Reset Progress'))

        await waitFor(() => expect(resetPositionMock).toHaveBeenCalledWith('audiobook', 21))
        expect(updatePositionMock).not.toHaveBeenCalled()
    })
})
