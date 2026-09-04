import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import BookDetailPage from './BookDetailPage'
import { formatDateTime } from '../lib/datetime'

// Isolate BookDetailPage from its heavier children/deps so this test only
// exercises the enrich-from-ABS toast logic.
const {
    getEbookMock,
    getAudiobookMock, getSettingsMock, enrichAudiobookFromAbsMock, getProgressMock, getPositionMock,
    updatePositionMock, resetPairProgressMock, resetPositionMock, getDeviceIdMock, getDeviceNameMock,
} = vi.hoisted(() => ({
    getEbookMock: vi.fn(),
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
    getEbook: getEbookMock,
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
// The overlays surface the props the page hands them, so the reader/player
// handoff can be driven without rendering either for real. `player` is mutable
// so a test can put the page in "audiobook playing" state.
const player = vi.hoisted(() => ({
    play: vi.fn(),
    pause: vi.fn(),
    currentAudiobook: null,
    currentTime: 0,
}))

// The reader exposes its pending-save flush through a ref the page hands it
// (issue #158) — the mock installs a spy there so call ordering against
// player.play can be asserted.
const readerFlushSpy = vi.hoisted(() => vi.fn())

vi.mock('../components/EbookReader', () => ({
    default: (props) => {
        if (props.saveFlushRef) props.saveFlushRef.current = readerFlushSpy
        return (
            <div data-testid="reader" data-chapter={String(props.initialChapter)}>
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
                <button onClick={() => props.onSwitchToEbook(77, 900)}>to-ebook</button>
            )}
        </div>
    ),
}))
vi.mock('../components/CoverImg', () => ({ default: () => null }))
vi.mock('../contexts/AuthContext', () => ({ useAuth: () => ({ hasMinRole: () => true }) }))
vi.mock('../contexts/AudioPlayerContext', () => ({ useAudioPlayer: () => player }))

function renderPage(entry = '/book/audiobook/1538') {
    return render(
        <MemoryRouter initialEntries={[entry]}>
            <Routes>
                <Route path="/book/:type/:id" element={<BookDetailPage />} />
            </Routes>
        </MemoryRouter>
    )
}

beforeEach(() => {
    getEbookMock.mockReset()
    readerFlushSpy.mockReset()
    player.play.mockReset()
    player.pause.mockReset()
    player.currentAudiobook = null
    player.currentTime = 0
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

// Issue #216: `uploaded_at` arrives as naive UTC (no `Z`), so the page must
// parse it as UTC — not as local time, which shifted "Added" by the browser's
// offset and, west of UTC, onto the wrong calendar day for part of the day.
describe('BookDetailPage renders server timestamps as UTC (issue #216)', () => {
    it('shows "Added" at the UTC instant the server meant', async () => {
        getAudiobookMock.mockResolvedValue({
            id: 1538, title: 'Antiagon Fire', author: 'L. E. Modesitt Jr', cover_path: null,
            uploaded_at: '2026-08-22T14:03:00',
        })
        renderPage()

        const label = await screen.findByText('Added')
        const value = label.nextSibling.textContent
        expect(value).toBe(formatDateTime('2026-08-22T14:03:00', {
            year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
        }))
        // i.e. the UTC instant, not the one `new Date()` would read locally
        // (identical, and so not worth asserting, when the runner is on UTC).
        if (new Date(2026, 7, 22).getTimezoneOffset() !== 0) {
            expect(value).not.toBe(new Date('2026-08-22T14:03:00').toLocaleString(undefined, {
                year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
            }))
        }
    })
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

    it('marks a PAIRED book complete at pair scope, not at its media scope', async () => {
        // The pair's two halves share one canonical record. A media-scoped
        // write would leave the pair's own record un-finished, so the book
        // stays on Continue and reopens as unread (issue #274; contract
        // § Completion, "Pairs complete as a pair").
        getAudiobookMock.mockResolvedValue({
            id: 1538, title: 'Antiagon Fire', author: 'L. E. Modesitt Jr', cover_path: null, pair_id: 77,
        })
        getProgressMock.mockResolvedValue({ is_completed: false, audio_position_ms: 1000 })
        renderPage()

        fireEvent.click(await screen.findByRole('button', { name: /Mark Complete/ }))

        await waitFor(() => expect(updatePositionMock).toHaveBeenCalled())
        expect(updatePositionMock).toHaveBeenCalledWith('pair', 77, expect.objectContaining({
            is_completed: true,
            device_id: 'device-abc',
            device_name: 'Web · Chrome',
            captured_at: expect.any(String),
        }))
        // A completion toggle carries no anchor and claims no format.
        expect(updatePositionMock.mock.calls[0][2]).not.toHaveProperty('source')
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

describe('BookDetailPage reader/player handoff', () => {
    // Both directions used to go through the legacy bookmark endpoint; they
    // now read and write the canonical record (issue #102).

    function renderEbookPage() {
        getEbookMock.mockResolvedValue({
            id: 900, title: 'Antiagon Fire', author: 'L. E. Modesitt Jr',
            cover_path: null, format: 'epub', pair_id: 77,
            paired_with: { id: 1538, title: 'Antiagon Fire (audio)' },
        })
        return render(
            <MemoryRouter initialEntries={['/book/ebook/900']}>
                <Routes>
                    <Route path="/book/:type/:id" element={<BookDetailPage />} />
                </Routes>
            </MemoryRouter>
        )
    }

    async function openTheReader() {
        renderEbookPage()
        fireEvent.click(await screen.findByRole('button', { name: /^Read$/ }))
        return await screen.findByTestId('reader')
    }

    // Issue #212: the handoff is a resume, so it backs up RESUME_REWIND_SECONDS
    // (5 s) exactly like unpausing and exactly like Android's `epubToAudioText`.
    // The *stored* anchor stays the raw matched point -- the rewind is applied
    // here, at the call site, not to what `doSave` wrote.
    it('switching to audio rewinds 5s off the canonical anchor', async () => {
        getPositionMock.mockResolvedValue({ audio_position_ms: 42000 })
        await openTheReader()

        fireEvent.click(screen.getByText('to-audio'))

        await waitFor(() => expect(getPositionMock).toHaveBeenCalledWith('pair', 77))
        await waitFor(() => expect(player.play).toHaveBeenCalledWith(
            1538, expect.anything(), 37000, 900))
    })

    it('clamps the handoff rewind at 0 for an anchor under 5s', async () => {
        getPositionMock.mockResolvedValue({ audio_position_ms: 2000 })
        await openTheReader()

        fireEvent.click(screen.getByText('to-audio'))

        await waitFor(() => expect(player.play).toHaveBeenCalledWith(
            1538, expect.anything(), 0, 900))
    })

    it('falls back to the progress projection when the record has no audio position', async () => {
        getPositionMock.mockResolvedValue({ audio_position_ms: 0 })
        getProgressMock.mockResolvedValue({ audio_position_ms: 9000 })
        await openTheReader()

        fireEvent.click(screen.getByText('to-audio'))

        await waitFor(() => expect(getProgressMock).toHaveBeenCalledWith('audiobook', 1538))
        await waitFor(() => expect(player.play).toHaveBeenCalledWith(
            1538, expect.anything(), 4000, 900))
    })

    it('issues the reader flush before starting the audiobook (issue #158)', async () => {
        // Without this ordering the reader's pending page-turn save is dropped
        // and the player's first write stamps source=audiobook, so the pair
        // reopens in the audiobook at a stale text position.
        getPositionMock.mockResolvedValue({ audio_position_ms: 42000 })
        await openTheReader()

        fireEvent.click(screen.getByText('to-audio'))

        await waitFor(() => expect(player.play).toHaveBeenCalled())
        expect(readerFlushSpy).toHaveBeenCalled()
        expect(Math.min(...readerFlushSpy.mock.invocationCallOrder))
            .toBeLessThan(Math.min(...player.play.mock.invocationCallOrder))
    })

    it('switching to the ebook saves the audio position and opens at the returned chapter', async () => {
        getAudiobookMock.mockResolvedValue({
            id: 1538, title: 'Antiagon Fire', author: 'L. E. Modesitt Jr',
            cover_path: null, pair_id: 77, paired_with: { id: 900, title: 'Antiagon Fire' },
        })
        player.currentAudiobook = { id: 1538, title: 'Antiagon Fire' }
        player.currentTime = 12.7
        updatePositionMock.mockResolvedValue({ epub_chapter: 4 })

        renderPage()
        fireEvent.click(await screen.findByRole('button', { name: /Listen/ }))
        fireEvent.click(await screen.findByText('to-ebook'))

        expect(player.pause).toHaveBeenCalled()
        await waitFor(() => expect(updatePositionMock).toHaveBeenCalledWith(
            'pair', 77, expect.objectContaining({
                source: 'audiobook',
                audio_position_ms: 12700,
                device_id: 'device-abc',
                device_name: 'Web · Chrome',
                captured_at: expect.any(String),
            })))

        const reader = await screen.findByTestId('reader')
        expect(reader).toHaveAttribute('data-chapter', '4')
    })
})


// ---------------------------------------------------------------------------
// Issue #286. The description is remote text — it arrives from Audiobookshelf,
// which pulls it from third-party metadata providers. It is rendered through
// react-markdown, which escapes raw HTML and neutralises `javascript:` URLs by
// default, and that default is load-bearing: the 30-day refresh token sits in
// localStorage where any injected script can read it.
//
// ABS descriptions currently show their literal <p> tags, so there is a
// standing temptation to "fix" this with rehype-raw. This test is what fails if
// anyone does.
// ---------------------------------------------------------------------------
describe('BookDetailPage description rendering (issue #286)', () => {
    const HOSTILE = '<img src=x onerror=alert(1)> [click](javascript:alert(1))'

    it('renders hostile markdown without injecting HTML or a javascript: link', async () => {
        getAudiobookMock.mockResolvedValue({
            id: 1538, title: 'Antiagon Fire', author: 'L. E. Modesitt Jr',
            cover_path: null, description: HOSTILE,
        })

        const { container } = renderPage()
        // The title appears in more than one place (heading and paired-with),
        // so wait on the description instead — it is what this test is about.
        await screen.findByText(/click/)

        // No element from the raw HTML made it into the DOM.
        expect(container.querySelector('img[src="x"]')).toBeNull()

        // And no anchor carries a javascript: URL.
        const hrefs = [...container.querySelectorAll('a')].map(a => a.getAttribute('href') || '')
        expect(hrefs.some(h => h.trim().toLowerCase().startsWith('javascript:'))).toBe(false)
    })
})


// ---------------------------------------------------------------------------
// Issue #267: arriving here from the app-wide mini-player's "switch to ebook".
//
// Both pages that already had the handoff open the reader through component
// state, not a URL, so the shell cannot hand the user over by navigating alone
// — it carries the intent in router state and this page acts on it. Without
// this half, the mini-player's Read button lands the user on the book's detail
// page and stops there, which is worse than not offering it.
// ---------------------------------------------------------------------------
describe('BookDetailPage opens the reader from router state (issue #267)', () => {
    beforeEach(() => {
        getEbookMock.mockResolvedValue({
            id: 900, title: 'Antiagon Fire', author: 'L. E. Modesitt Jr',
            cover_path: null, pair_id: 77,
        })
    })

    it('opens the reader at the chapter the handoff carried', async () => {
        renderPage({ pathname: '/book/ebook/900', state: { openReader: true, initialChapter: 3 } })

        const reader = await screen.findByTestId('reader')
        expect(reader.getAttribute('data-chapter')).toBe('3')
    })

    it('still opens the reader when the handoff had no chapter to give', async () => {
        renderPage({ pathname: '/book/ebook/900', state: { openReader: true, initialChapter: null } })

        const reader = await screen.findByTestId('reader')
        expect(reader.getAttribute('data-chapter')).toBe('null')
    })

    it('leaves the reader closed on a normal visit', async () => {
        renderPage('/book/ebook/900')

        await screen.findByText('L. E. Modesitt Jr')
        expect(screen.queryByTestId('reader')).toBeNull()
    })
})
