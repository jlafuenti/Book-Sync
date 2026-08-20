import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import ContinuePage from './ContinuePage'

const {
    getAllProgressMock, getEbooksMock, getAudiobooksMock, getPairsMock,
    updatePositionMock, resetPairProgressMock, resetPositionMock, getProgressMock, getPositionMock,
    getAccessTokenMock, getDeviceIdMock, getDeviceNameMock, coverSrcMock,
} = vi.hoisted(() => ({
    getAllProgressMock: vi.fn(),
    getEbooksMock: vi.fn(),
    getAudiobooksMock: vi.fn(),
    getPairsMock: vi.fn(),
    updatePositionMock: vi.fn(),
    resetPairProgressMock: vi.fn(),
    resetPositionMock: vi.fn(),
    getProgressMock: vi.fn(),
    getPositionMock: vi.fn(),
    getAccessTokenMock: vi.fn(() => 'token'),
    getDeviceIdMock: vi.fn(() => 'device-abc'),
    getDeviceNameMock: vi.fn(() => 'Web · Chrome'),
    coverSrcMock: vi.fn(async (path) => `${path}?token=scoped`),
}))

vi.mock('../api', () => ({
    getAllProgress: getAllProgressMock,
    getEbooks: getEbooksMock,
    getAudiobooks: getAudiobooksMock,
    getPairs: getPairsMock,
    updatePosition: updatePositionMock,
    resetPairProgress: resetPairProgressMock,
    resetPosition: resetPositionMock,
    getProgress: getProgressMock,
    getPosition: getPositionMock,
    getAccessToken: getAccessTokenMock,
    getDeviceId: getDeviceIdMock,
    getDeviceName: getDeviceNameMock,
    coverSrc: coverSrcMock,
}))

// Isolate ContinuePage from its heavier children -- EbookReader pulls in
// epubjs. The overlays still surface the props the page hands them, so the
// reader/player handoff can be driven without rendering either for real.
// `player` is mutable so a test can put the page in "audiobook playing" state.
const player = vi.hoisted(() => ({
    play: vi.fn(),
    pause: vi.fn(),
    currentAudiobook: null,
    pairedEbookId: null,
    currentTime: 0,
}))

vi.mock('../contexts/AudioPlayerContext', () => ({
    useAudioPlayer: () => player,
}))
vi.mock('../components/EbookReader', () => ({
    default: (props) => (
        <div
            data-testid="reader"
            data-ebook-id={String(props.ebookId)}
            data-chapter={String(props.initialChapter)}
            data-preview={String(props.initialTextPreview)}
        >
            {props.onSwitchToAudio && (
                <button onClick={props.onSwitchToAudio}>to-audio</button>
            )}
        </div>
    ),
}))
vi.mock('../components/AudioPlayer', () => ({
    AudioPlayerView: (props) => (
        <div data-testid="player">
            {props.onSwitchToEbook && (
                <button onClick={() => props.onSwitchToEbook(100)}>to-ebook</button>
            )}
        </div>
    ),
}))

beforeEach(() => {
    getAllProgressMock.mockReset().mockResolvedValue([])
    getEbooksMock.mockReset().mockResolvedValue([])
    getAudiobooksMock.mockReset().mockResolvedValue([])
    getPairsMock.mockReset().mockResolvedValue([])
    updatePositionMock.mockReset().mockResolvedValue({})
    resetPairProgressMock.mockReset().mockResolvedValue({})
    getProgressMock.mockReset().mockResolvedValue(null)
    getPositionMock.mockReset().mockResolvedValue(null)
    resetPositionMock.mockReset().mockResolvedValue({ status: 'ok' })
    getAccessTokenMock.mockReset().mockReturnValue('token')
    player.play.mockReset()
    player.pause.mockReset()
    player.currentAudiobook = null
    player.pairedEbookId = null
    player.currentTime = 0
    getDeviceIdMock.mockReset().mockReturnValue('device-abc')
    getDeviceNameMock.mockReset().mockReturnValue('Web · Chrome')
    coverSrcMock.mockReset().mockImplementation(async (path) => `${path}?token=scoped`)
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

    it('marks a pair complete with one canonical write carrying the device fields', async () => {
        setupLibrary()
        renderPage()

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
        renderPage()

        const card = await openMenu('Ebook A')
        fireEvent.click(within(card).getByText('Mark Complete'))

        await waitFor(() => expect(updatePositionMock).toHaveBeenCalledWith('ebook', 11, expect.objectContaining({
            is_completed: true, device_id: 'device-abc', device_name: 'Web · Chrome', captured_at: expect.any(String),
        })))
    })

    it('calls the pair-level DELETE for a paired Reset Progress instead of zero-writing each leg', async () => {
        // The old per-leg zero-write left the canonical
        // bookmark in place, which re-seeded progress right back (issue:
        // reset buttons not actually resetting). The pair-scoped DELETE
        // removes the bookmark + hints + progress rows server-side.
        setupLibrary()
        renderPage()

        const card = await openMenu('Pair Ebook')
        fireEvent.click(within(card).getByText('Reset Progress'))

        await waitFor(() => expect(resetPairProgressMock).toHaveBeenCalledWith(100))
        expect(updatePositionMock).not.toHaveBeenCalled()
        expect(resetPositionMock).not.toHaveBeenCalled()
    })

    it('resets a standalone ebook with the scoped DELETE, not a zero-write', async () => {
        setupLibrary()
        renderPage()

        const card = await openMenu('Ebook A')
        fireEvent.click(within(card).getByText('Reset Progress'))

        await waitFor(() => expect(resetPositionMock).toHaveBeenCalledWith('ebook', 11))
        expect(updatePositionMock).not.toHaveBeenCalled()
    })

    it('resets a standalone audiobook with the scoped DELETE, not a zero-write', async () => {
        setupLibrary()
        renderPage()

        const card = await openMenu('Audiobook A')
        fireEvent.click(within(card).getByText('Reset Progress'))

        await waitFor(() => expect(resetPositionMock).toHaveBeenCalledWith('audiobook', 21))
        expect(updatePositionMock).not.toHaveBeenCalled()
    })
})

describe('ContinuePage reader/player handoff', () => {
    // These paths used to read a legacy bookmark and a `user_progress.epub_cfi`
    // snapshot; both are gone (issue #102). What matters now: the handoff goes
    // through the canonical position endpoint, and the reader is handed the
    // portable anchor rather than a CFI.

    function setupPair() {
        getEbooksMock.mockResolvedValue([{ id: 10, title: 'Pair Ebook', cover_path: null }])
        getAudiobooksMock.mockResolvedValue([
            { id: 20, title: 'Pair Audiobook', cover_path: null, duration_seconds: 3600 },
        ])
        getPairsMock.mockResolvedValue([{ id: 100, ebook: { id: 10 }, audiobook: { id: 20 } }])
        getAllProgressMock.mockResolvedValue([
            { id: 1, media_type: 'ebook', ebook_id: 10, epub_progress_percent: 20,
              epub_chapter: 7, book_pair_id: 100, is_completed: false,
              updated_at: '2024-01-02T00:00:00Z' },
            { id: 2, media_type: 'audiobook', audiobook_id: 20, audio_position_ms: 500,
              book_pair_id: 100, is_completed: false, updated_at: '2024-01-01T00:00:00Z' },
        ])
    }

    async function chooseFromTheMenu(label) {
        const titleEl = await screen.findByText('Pair Ebook')
        const card = titleEl.closest('.continue-card')
        fireEvent.click(card.querySelector('.continue-card-menu'))
        fireEvent.click(within(card).getByText(label))
    }

    async function openTheReader() {
        render(<MemoryRouter><ContinuePage /></MemoryRouter>)
        await chooseFromTheMenu('Continue Reading')
        return await screen.findByTestId('reader')
    }

    it('opens the reader on the stored chapter and hands it no CFI', async () => {
        setupPair()
        const reader = await openTheReader()

        expect(reader).toHaveAttribute('data-ebook-id', '10')
        expect(reader).toHaveAttribute('data-chapter', '7')
    })

    it('switching to audio takes the audio anchor from the canonical record', async () => {
        setupPair()
        getPositionMock.mockResolvedValue({ audio_position_ms: 42000 })
        await openTheReader()

        fireEvent.click(screen.getByText('to-audio'))

        await waitFor(() => expect(getPositionMock).toHaveBeenCalledWith('pair', 100))
        await waitFor(() => expect(player.play).toHaveBeenCalledWith(
            20, expect.anything(), 42000, 10))
        expect(getProgressMock).not.toHaveBeenCalled()
    })

    it('falls back to the progress projection when the record has no audio position', async () => {
        setupPair()
        getPositionMock.mockResolvedValue({ audio_position_ms: 0 })
        getProgressMock.mockResolvedValue({ audio_position_ms: 9000 })
        await openTheReader()

        fireEvent.click(screen.getByText('to-audio'))

        await waitFor(() => expect(getProgressMock).toHaveBeenCalledWith('audiobook', 20))
        await waitFor(() => expect(player.play).toHaveBeenCalledWith(
            20, expect.anything(), 9000, 10))
    })

    it('switching to the ebook saves the audio position and opens at the returned anchor', async () => {
        setupPair()
        player.currentAudiobook = { id: 20, title: 'Pair Audiobook' }
        player.pairedEbookId = 10
        player.currentTime = 12.7
        updatePositionMock.mockResolvedValue({ epub_chapter: 4, epub_text_preview: 'a line' })

        render(<MemoryRouter><ContinuePage /></MemoryRouter>)
        await chooseFromTheMenu('Continue Listening')
        fireEvent.click(await screen.findByText('to-ebook'))

        expect(player.pause).toHaveBeenCalled()
        await waitFor(() => expect(updatePositionMock).toHaveBeenCalledWith(
            'pair', 100, expect.objectContaining({
                source: 'audiobook',
                audio_position_ms: 12700,
                device_id: 'device-abc',
                captured_at: expect.any(String),
            })))

        const reader = await screen.findByTestId('reader')
        expect(reader).toHaveAttribute('data-chapter', '4')
        expect(reader).toHaveAttribute('data-preview', 'a line')
    })

    it('still opens the reader when the position save fails offline', async () => {
        setupPair()
        player.currentAudiobook = { id: 20, title: 'Pair Audiobook' }
        player.pairedEbookId = 10
        player.currentTime = 5
        updatePositionMock.mockRejectedValue(new Error('offline'))

        render(<MemoryRouter><ContinuePage /></MemoryRouter>)
        await chooseFromTheMenu('Continue Listening')
        fireEvent.click(await screen.findByText('to-ebook'))

        const reader = await screen.findByTestId('reader')
        expect(reader).toHaveAttribute('data-chapter', 'null')
    })
})

describe('ContinuePage covers (issue #126)', () => {
    it('resolves cover URLs through coverSrc, not a hand-built access-token URL', async () => {
        // The old inline `${cover_path}?token=${getAccessToken()}` left a '#' in the
        // filename unencoded (the browser then dropped the query as a fragment -> 401)
        // and put the long-lived access token in the URL, which #50 removed.
        getEbooksMock.mockResolvedValue([
            { id: 11, title: 'Suspect', cover_path: '/api/files/covers/Private_#1_Suspect.jpg' },
        ])
        getAudiobooksMock.mockResolvedValue([])
        getPairsMock.mockResolvedValue([])
        getAllProgressMock.mockResolvedValue([
            {
                id: 1, media_type: 'ebook', ebook_id: 11, epub_progress_percent: 40,
                epub_chapter: 0, book_pair_id: null, is_completed: false,
                updated_at: '2024-01-02T00:00:00Z',
            },
        ])

        render(<MemoryRouter><ContinuePage /></MemoryRouter>)

        const img = await screen.findByAltText('Suspect')
        await waitFor(() => expect(img).toHaveAttribute(
            'src', '/api/files/covers/Private_#1_Suspect.jpg?token=scoped',
        ))
        expect(coverSrcMock).toHaveBeenCalledWith('/api/files/covers/Private_#1_Suspect.jpg')
        expect(getAccessTokenMock).not.toHaveBeenCalled()
    })
})
