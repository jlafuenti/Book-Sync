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
// AudioPlayer/CoverImg aren't relevant to the logic under test. The two
// overlays still surface the props HomePage hands them, so the reader/player
// handoff can be driven without rendering either for real. `player` is mutable
// so a test can put the page in "audiobook playing" state.
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
// The reader exposes its pending-save flush through a ref the page hands it
// (issue #158) — the mock installs a spy there so call ordering against
// player.play can be asserted.
const readerFlushSpy = vi.hoisted(() => vi.fn())

vi.mock('../components/EbookReader', () => ({
    default: (props) => {
        if (props.saveFlushRef) props.saveFlushRef.current = readerFlushSpy
        return (
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
        )
    },
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
vi.mock('../components/CoverImg', () => ({ default: () => null }))

// Viewport width is a render-time branch on this page (issue #273): the
// Continue card's actions are a hover overlay on desktop and a tap target on
// mobile. Mutable so one file can drive both without a second mock factory.
const mobileRef = vi.hoisted(() => ({ value: false }))
vi.mock('../hooks/useIsMobile', () => ({ default: () => mobileRef.value }))

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
    readerFlushSpy.mockReset()
    mobileRef.value = false
    player.play.mockReset()
    player.pause.mockReset()
    player.currentAudiobook = null
    player.pairedEbookId = null
    player.currentTime = 0

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

    it('calls the pair-level DELETE for a paired Reset Progress instead of zero-writing each leg', async () => {
        // The old per-leg zero-write left the canonical bookmark in place,
        // which re-seeded progress right back (issue: reset buttons not
        // actually resetting). The pair-scoped DELETE removes the bookmark +
        // hints + progress rows server-side.
        setupLibrary()
        renderHome()

        const card = await openMenu('Pair Ebook')
        fireEvent.click(within(card).getByText('Reset Progress'))

        await waitFor(() => expect(resetPairProgressMock).toHaveBeenCalledWith(100))
        expect(updatePositionMock).not.toHaveBeenCalled()
        expect(resetPositionMock).not.toHaveBeenCalled()
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

describe('HomePage reader/player handoff', () => {
    // These paths used to read a legacy bookmark and a `user_progress.epub_cfi`
    // snapshot; both are gone (issue #102). What matters now: the handoff goes
    // through the canonical position endpoint, and the reader is handed the
    // portable anchor rather than a CFI it would have to distrust anyway.

    // A Continue card's primary action is the card itself; which overlay opens
    // follows `lastFormat`, i.e. whichever half was touched most recently.
    function setupPair({ lastFormat = 'ebook' } = {}) {
        const ebookAt = lastFormat === 'ebook' ? '2024-01-02T00:00:00Z' : '2024-01-01T00:00:00Z'
        const audioAt = lastFormat === 'ebook' ? '2024-01-01T00:00:00Z' : '2024-01-02T00:00:00Z'
        getEbooksMock.mockResolvedValue([{ id: 10, title: 'Pair Ebook', cover_path: null }])
        getAudiobooksMock.mockResolvedValue([
            { id: 20, title: 'Pair Audiobook', cover_path: null, duration_seconds: 3600 },
        ])
        getPairsMock.mockResolvedValue([{ id: 100, ebook: { id: 10 }, audiobook: { id: 20 } }])
        getAllProgressMock.mockResolvedValue([
            { id: 1, media_type: 'ebook', ebook_id: 10, epub_progress_percent: 20,
              epub_chapter: 7, book_pair_id: 100, is_completed: false,
              updated_at: ebookAt },
            { id: 2, media_type: 'audiobook', audiobook_id: 20, audio_position_ms: 500,
              book_pair_id: 100, is_completed: false, updated_at: audioAt },
        ])
    }

    // The card is titled after whichever half is `primary`, so it follows
    // lastFormat too.
    async function clickTheContinueCard(title) {
        const matches = await screen.findAllByText(title)
        const card = matches.map((el) => el.closest('.continue-size')).find(Boolean)
        fireEvent.click(card)
    }

    async function openTheReader() {
        render(<MemoryRouter><HomePage /></MemoryRouter>)
        await clickTheContinueCard('Pair Ebook')
        return await screen.findByTestId('reader')
    }

    async function openThePlayerAndSwitchBack() {
        render(<MemoryRouter><HomePage /></MemoryRouter>)
        await clickTheContinueCard('Pair Audiobook')
        fireEvent.click(await screen.findByText('to-ebook'))
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
        // The record answered, so the projection is not consulted.
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

    it('issues the reader flush before starting the audiobook (issue #158)', async () => {
        // Without this ordering the reader's pending page-turn save is dropped
        // and the player's first write stamps source=audiobook, so the pair
        // reopens in the audiobook at a stale text position.
        setupPair()
        getPositionMock.mockResolvedValue({ audio_position_ms: 42000 })
        await openTheReader()

        fireEvent.click(screen.getByText('to-audio'))

        await waitFor(() => expect(player.play).toHaveBeenCalled())
        expect(readerFlushSpy).toHaveBeenCalled()
        expect(Math.min(...readerFlushSpy.mock.invocationCallOrder))
            .toBeLessThan(Math.min(...player.play.mock.invocationCallOrder))
    })

    it('starts at 0 rather than throwing when both reads fail offline', async () => {
        setupPair()
        getPositionMock.mockRejectedValue(new Error('offline'))
        getProgressMock.mockRejectedValue(new Error('offline'))
        await openTheReader()

        fireEvent.click(screen.getByText('to-audio'))

        await waitFor(() => expect(player.play).toHaveBeenCalledWith(
            20, expect.anything(), 0, 10))
    })

    it('switching to the ebook saves the audio position and opens at the returned anchor', async () => {
        setupPair({ lastFormat: 'audiobook' })
        player.currentAudiobook = { id: 20, title: 'Pair Audiobook' }
        player.pairedEbookId = 10
        player.currentTime = 12.7
        updatePositionMock.mockResolvedValue({ epub_chapter: 4, epub_text_preview: 'a line' })

        await openThePlayerAndSwitchBack()

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
        setupPair({ lastFormat: 'audiobook' })
        player.currentAudiobook = { id: 20, title: 'Pair Audiobook' }
        player.pairedEbookId = 10
        player.currentTime = 5
        updatePositionMock.mockRejectedValue(new Error('offline'))

        await openThePlayerAndSwitchBack()

        // No anchor to hand over, but the reader must still open — it fetches
        // the canonical position itself anyway.
        const reader = await screen.findByTestId('reader')
        expect(reader).toHaveAttribute('data-chapter', 'null')
    })
})

// Issue #273: the Continue card's Read / Listen / Mark Complete / Reset /
// Details controls were built unconditionally and then painted out of
// existence on mobile (`display: none !important`), leaving a phone with one
// gesture — tap the cover — and no way to choose a format, finish a book or
// reset it from Home. The replacement is a persistent Read/Listen pair plus a
// tap-opened sheet for the rest, calling the same handlers as desktop.
describe('HomePage Continue card actions on mobile', () => {
    beforeEach(() => {
        mobileRef.value = true
        getEbooksMock.mockResolvedValue([{ id: 10, title: 'Pair Ebook', cover_path: null }])
        getAudiobooksMock.mockResolvedValue([
            { id: 20, title: 'Pair Audiobook', cover_path: null, duration_seconds: 3600 },
        ])
        getPairsMock.mockResolvedValue([{ id: 100, ebook: { id: 10 }, audiobook: { id: 20 } }])
        getAllProgressMock.mockResolvedValue([
            { id: 1, media_type: 'ebook', ebook_id: 10, epub_progress_percent: 20, epub_chapter: 3,
              book_pair_id: 100, is_completed: false, updated_at: '2024-01-01T00:00:00Z' },
        ])
    })

    async function continueCard() {
        const matches = await screen.findAllByText('Pair Ebook')
        return matches.map((el) => el.closest('.continue-size')).find(Boolean)
    }

    function renderHome() {
        return render(<MemoryRouter><HomePage /></MemoryRouter>)
    }

    // The bug was invisible to the DOM: the controls were rendered and then
    // hidden by CSS (`.home-book-card-overlay`/`-menu { display: none
    // !important }` in the mobile block). So "reachable on mobile" has to mean
    // "not inside either of the two containers that stylesheet blanks out".
    function reachable(card, title) {
        const btn = within(card).getByTitle(title)
        expect(btn.closest('.home-book-card-overlay'), `${title} sits in the hover overlay`).toBeNull()
        expect(btn.closest('.home-book-card-menu'), `${title} sits in the hover menu`).toBeNull()
        return btn
    }

    async function openSheet(card, name) {
        fireEvent.click(reachable(card, 'More options'))
        const item = await screen.findByRole('button', { name })
        expect(item.closest('.home-book-card-menu'), `${name} sits in the hover menu`).toBeNull()
        return item
    }

    it('opens the reader from a Read control that is on the card, not behind hover', async () => {
        renderHome()
        const card = await continueCard()

        fireEvent.click(reachable(card, 'Read'))

        const reader = await screen.findByTestId('reader')
        expect(reader).toHaveAttribute('data-ebook-id', '10')
    })

    it('starts the audiobook from a Listen control on the card', async () => {
        renderHome()
        const card = await continueCard()

        fireEvent.click(reachable(card, 'Listen'))

        await waitFor(() => expect(player.play).toHaveBeenCalled())
        expect(player.play.mock.calls[0][0]).toBe(20)
    })

    it('reaches Mark Complete through the card sheet, with the same canonical write as desktop', async () => {
        renderHome()
        const card = await continueCard()

        fireEvent.click(await openSheet(card, 'Mark Complete'))

        await waitFor(() => expect(updatePositionMock).toHaveBeenCalledWith('pair', 100, expect.objectContaining({
            is_completed: true, device_id: 'device-abc', device_name: 'Web · Chrome', captured_at: expect.any(String),
        })))
        expect(updatePositionMock).toHaveBeenCalledTimes(1)
    })

    it('reaches Reset Progress through the card sheet, using the pair-level DELETE', async () => {
        renderHome()
        const card = await continueCard()

        fireEvent.click(await openSheet(card, 'Reset Progress'))

        await waitFor(() => expect(resetPairProgressMock).toHaveBeenCalledWith(100))
        expect(updatePositionMock).not.toHaveBeenCalled()
    })

    it('offers View Details in the sheet and closes the sheet on choosing it', async () => {
        renderHome()
        const card = await continueCard()

        fireEvent.click(await openSheet(card, 'View Details'))

        await waitFor(() => expect(screen.queryByRole('button', { name: 'View Details' })).not.toBeInTheDocument())
    })
})
