import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import EbookReader, { resolveInitialDisplayTarget } from './EbookReader'

const {
    fetchEbookBlobMock, updateProgressMock, updateBookmarkMock, matchTextToAudioMock,
    getDeviceIdMock, getDeviceNameMock, ePubMock,
} = vi.hoisted(() => ({
    fetchEbookBlobMock: vi.fn(),
    updateProgressMock: vi.fn(),
    updateBookmarkMock: vi.fn(),
    matchTextToAudioMock: vi.fn(),
    getDeviceIdMock: vi.fn(() => 'device-abc'),
    getDeviceNameMock: vi.fn(() => 'Web · Chrome'),
    ePubMock: vi.fn(),
}))

vi.mock('../api', () => ({
    fetchEbookBlob: fetchEbookBlobMock,
    updateProgress: updateProgressMock,
    updateBookmark: updateBookmarkMock,
    matchTextToAudio: matchTextToAudioMock,
    getDeviceId: getDeviceIdMock,
    getDeviceName: getDeviceNameMock,
}))

// Lean fake for epubjs -- a real epub.js needs canvas/iframe machinery jsdom
// can't provide. This fake only implements the surface EbookReader.jsx
// actually touches: renderTo/themes/hooks/display/on('relocated')/getContents,
// plus the ready/locations bookkeeping done after initial render.
vi.mock('epubjs', () => ({ default: ePubMock }))

/**
 * Builds a fake epub.js `book` + `rendition` pair. `bodyText` controls what
 * EbookReader's extractVisibleText() fallback path sees (via
 * rendition.getContents()[0].document.body.innerText) -- this is what drives
 * doSave()'s three-way branch (match / no-match / text-too-short), since
 * `bookRef.current.epubcfi` is intentionally left undefined so the CFI-based
 * primary extraction path is skipped and the fallback always runs.
 */
function makeFakeBook(bodyText) {
    const handlers = {}
    const rendition = {
        themes: { default: vi.fn() },
        hooks: { content: { register: vi.fn() } },
        on: vi.fn((event, cb) => { handlers[event] = cb }),
        display: vi.fn().mockResolvedValue(undefined),
        getContents: vi.fn(() => [{ document: { body: { innerText: bodyText } } }]),
        reportLocation: vi.fn(),
        prev: vi.fn(),
        next: vi.fn(),
    }
    const book = {
        renderTo: vi.fn(() => rendition),
        loaded: { navigation: Promise.resolve({ toc: [] }) },
        spine: { items: [{ href: 'ch1.xhtml' }], get: vi.fn(() => null) },
        locations: { generate: vi.fn().mockResolvedValue(undefined) },
        ready: Promise.resolve(),
        destroy: vi.fn(),
    }
    return { book, rendition, handlers }
}

beforeEach(() => {
    fetchEbookBlobMock.mockReset().mockResolvedValue(new ArrayBuffer(0))
    updateProgressMock.mockReset().mockResolvedValue({})
    updateBookmarkMock.mockReset().mockResolvedValue({})
    matchTextToAudioMock.mockReset()
    getDeviceIdMock.mockReset().mockReturnValue('device-abc')
    getDeviceNameMock.mockReset().mockReturnValue('Web · Chrome')
    ePubMock.mockReset()
})

const LONG_TEXT = 'The quick brown fox jumps over the lazy dog and then runs away quickly into the forest without looking back at all.'

async function setupReader(bodyText) {
    const { book, rendition, handlers } = makeFakeBook(bodyText)
    ePubMock.mockReturnValue(book)

    render(
        <EbookReader
            ebookId={7}
            pairId={42}
            initialCfi={null}
            initialChapter={null}
            initialTextPreview={null}
            bookTitle="Test Book"
            onClose={vi.fn()}
        />
    )

    await waitFor(() => expect(rendition.display).toHaveBeenCalled())

    // Simulate a page-turn: this sets currentCfi/progressPercent (state)
    // and the current spine index (ref) that doSave reads from.
    act(() => {
        handlers.relocated({
            start: { cfi: 'cfi-test', percentage: 0.5, displayed: { page: 1, total: 1 }, href: 'ch1.xhtml' },
        })
    })

    return { book, rendition }
}

describe('EbookReader doSave device attribution (issue #54)', () => {
    it('sends device fields + a shared captured_at on the progress write for every save', async () => {
        await setupReader(LONG_TEXT)
        matchTextToAudioMock.mockResolvedValue(null)

        fireEvent.click(screen.getByTitle('Save position'))

        await waitFor(() => expect(updateProgressMock).toHaveBeenCalled())
        expect(updateProgressMock).toHaveBeenCalledWith('ebook', 7, expect.objectContaining({
            epub_cfi: 'cfi-test',
            book_pair_id: 42,
            device_id: 'device-abc',
            device_name: 'Web · Chrome',
            captured_at: expect.any(String),
        }))
    })

    it('match branch: writes the matched audio position + device fields to the bookmark', async () => {
        await setupReader(LONG_TEXT)
        matchTextToAudioMock.mockResolvedValue({
            epub_chapter: 2, epub_sentence_index: 5, audio_position_ms: 12345, preview: 'a preview',
        })

        fireEvent.click(screen.getByTitle('Save position'))

        await waitFor(() => expect(updateBookmarkMock).toHaveBeenCalled())
        expect(matchTextToAudioMock).toHaveBeenCalled()
        expect(updateBookmarkMock).toHaveBeenCalledWith(42, expect.objectContaining({
            source: 'ebook',
            epub_chapter: 2,
            epub_sentence_index: 5,
            audio_position_ms: 12345,
            device_id: 'device-abc',
            device_name: 'Web · Chrome',
            captured_at: expect.any(String),
        }))

        // Progress and bookmark writes from the same save share one timestamp.
        const progressCapturedAt = updateProgressMock.mock.calls[0][2].captured_at
        const bookmarkCapturedAt = updateBookmarkMock.mock.calls[0][1].captured_at
        expect(bookmarkCapturedAt).toBe(progressCapturedAt)
    })

    it('no-match branch: saves the epub chapter only, still with device fields', async () => {
        await setupReader(LONG_TEXT)
        matchTextToAudioMock.mockResolvedValue(null)

        fireEvent.click(screen.getByTitle('Save position'))

        await waitFor(() => expect(updateBookmarkMock).toHaveBeenCalled())
        expect(matchTextToAudioMock).toHaveBeenCalled()
        expect(updateBookmarkMock).toHaveBeenCalledWith(42, expect.objectContaining({
            source: 'ebook',
            epub_chapter: 0,
            device_id: 'device-abc',
            device_name: 'Web · Chrome',
            captured_at: expect.any(String),
        }))
        expect(updateBookmarkMock.mock.calls[0][1]).not.toHaveProperty('audio_position_ms')
    })

    it('matchTextToAudio rejecting falls through to the no-match branch (pre-existing .catch, exercised here since this task shifted its line numbers)', async () => {
        await setupReader(LONG_TEXT)
        matchTextToAudioMock.mockRejectedValue(new Error('network down'))

        fireEvent.click(screen.getByTitle('Save position'))

        await waitFor(() => expect(updateBookmarkMock).toHaveBeenCalled())
        expect(updateBookmarkMock).toHaveBeenCalledWith(42, expect.objectContaining({
            source: 'ebook',
            epub_chapter: 0,
            device_id: 'device-abc',
            device_name: 'Web · Chrome',
            captured_at: expect.any(String),
        }))
        expect(updateBookmarkMock.mock.calls[0][1]).not.toHaveProperty('audio_position_ms')
    })

    it('text-too-short branch: skips matchTextToAudio and saves chapter only, with device fields', async () => {
        // Empty innerText -> extractVisibleText() returns '' (falsy), which is
        // < the >10-char threshold, so doSave must take the "too short" path
        // without ever calling matchTextToAudio.
        await setupReader('')

        fireEvent.click(screen.getByTitle('Save position'))

        await waitFor(() => expect(updateBookmarkMock).toHaveBeenCalled())
        expect(matchTextToAudioMock).not.toHaveBeenCalled()
        expect(updateBookmarkMock).toHaveBeenCalledWith(42, expect.objectContaining({
            source: 'ebook',
            epub_chapter: 0,
            device_id: 'device-abc',
            device_name: 'Web · Chrome',
            captured_at: expect.any(String),
        }))
    })
})

describe('EbookReader stale-conflict affordance (issue #54)', () => {
    it('shows a banner with the device name when the progress write is rejected by a different device', async () => {
        await setupReader(LONG_TEXT)
        matchTextToAudioMock.mockResolvedValue(null)
        updateProgressMock.mockResolvedValue({
            rejected: true,
            device_id: 'device-999',
            device_name: 'Phone',
            epub_cfi: 'cfi-newer',
        })

        fireEvent.click(screen.getByTitle('Save position'))

        await waitFor(() => expect(screen.getByText(/Newer position available from Phone/)).toBeInTheDocument())
    })

    it("Jump navigates to the rejected write's cfi and dismisses the banner -- never auto-navigates", async () => {
        const { rendition } = await setupReader(LONG_TEXT)
        matchTextToAudioMock.mockResolvedValue(null)
        updateProgressMock.mockResolvedValue({
            rejected: true,
            device_id: 'device-999',
            device_name: 'Phone',
            epub_cfi: 'cfi-newer',
        })

        // rendition.display was already called once during initial load (with
        // no args, since setupReader passes no initialCfi/initialChapter) --
        // record that count so we can prove Jump is a distinct, later call.
        const callsBeforeSave = rendition.display.mock.calls.length

        fireEvent.click(screen.getByTitle('Save position'))
        await waitFor(() => expect(screen.getByText('Jump')).toBeInTheDocument())

        // The banner rendering alone must never navigate.
        expect(rendition.display.mock.calls.length).toBe(callsBeforeSave)

        fireEvent.click(screen.getByText('Jump'))

        expect(rendition.display).toHaveBeenLastCalledWith('cfi-newer')
        expect(screen.queryByText(/Newer position available/)).not.toBeInTheDocument()
    })

    it("does not show the banner when the rejection echoes this device's own id (a retried write)", async () => {
        await setupReader(LONG_TEXT)
        matchTextToAudioMock.mockResolvedValue(null)
        updateProgressMock.mockResolvedValue({
            rejected: true,
            device_id: 'device-abc', // matches getDeviceIdMock's own id
            device_name: 'Web · Chrome',
            epub_cfi: 'cfi-newer',
        })

        fireEvent.click(screen.getByTitle('Save position'))
        await waitFor(() => expect(updateProgressMock).toHaveBeenCalled())

        expect(screen.queryByText(/Newer position available/)).not.toBeInTheDocument()
    })

    it('does not show the banner when the rejection has no epub_cfi to jump to', async () => {
        await setupReader(LONG_TEXT)
        matchTextToAudioMock.mockResolvedValue(null)
        updateProgressMock.mockResolvedValue({
            rejected: true,
            device_id: 'device-999',
            device_name: 'Phone',
            epub_cfi: null,
        })

        fireEvent.click(screen.getByTitle('Save position'))
        await waitFor(() => expect(updateProgressMock).toHaveBeenCalled())

        expect(screen.queryByText(/Newer position available/)).not.toBeInTheDocument()
    })

    // Bookmark rejections (review finding on Task 3): BookmarkResponse carries
    // no navigable epub_cfi, so a rejected updateBookmark(...) can only ever
    // surface a passive notice (no Jump button), never a jump target.
    it('shows a passive notice (no Jump button) when a bookmark write is rejected by a different device', async () => {
        await setupReader(LONG_TEXT)
        matchTextToAudioMock.mockResolvedValue(null)
        updateBookmarkMock.mockResolvedValue({
            rejected: true,
            device_id: 'device-999',
            device_name: 'Phone',
        })

        fireEvent.click(screen.getByTitle('Save position'))

        await waitFor(() => expect(screen.getByText(/Newer position available from Phone/)).toBeInTheDocument())
        expect(screen.queryByText('Jump')).not.toBeInTheDocument()
    })

    it("does not show a bookmark notice when the rejection echoes this device's own id (a retried write)", async () => {
        await setupReader(LONG_TEXT)
        matchTextToAudioMock.mockResolvedValue(null)
        updateBookmarkMock.mockResolvedValue({
            rejected: true,
            device_id: 'device-abc', // matches getDeviceIdMock's own id
            device_name: 'Web · Chrome',
        })

        fireEvent.click(screen.getByTitle('Save position'))
        await waitFor(() => expect(updateBookmarkMock).toHaveBeenCalled())

        expect(screen.queryByText(/Newer position available/)).not.toBeInTheDocument()
    })

    it('Dismiss clears the bookmark notice without navigating (there is no cfi to jump to)', async () => {
        const { rendition } = await setupReader(LONG_TEXT)
        matchTextToAudioMock.mockResolvedValue(null)
        updateBookmarkMock.mockResolvedValue({
            rejected: true,
            device_id: 'device-999',
            device_name: 'Phone',
        })

        const callsBeforeSave = rendition.display.mock.calls.length
        fireEvent.click(screen.getByTitle('Save position'))
        await waitFor(() => expect(screen.getByText('Dismiss')).toBeInTheDocument())

        fireEvent.click(screen.getByText('Dismiss'))

        expect(screen.queryByText(/Newer position available/)).not.toBeInTheDocument()
        expect(rendition.display.mock.calls.length).toBe(callsBeforeSave)
    })
})

describe('resolveInitialDisplayTarget — stale CFI (issue #40)', () => {
    // chapter + sentence is the portable cross-device anchor; epub_cfi is a
    // web-local hint. Android writes progress with chapter/percent and no CFI
    // (issue #61), so a leftover CFI can point at a chapter the user has long
    // left — following it reopens the book at the wrong page.
    const bookWith = (cfiIndex, itemCount = 8) => ({
        spine: {
            items: Array.from({ length: itemCount }, (_, i) => ({ href: `ch${i}.xhtml` })),
            get: vi.fn(() => (cfiIndex === null ? null : { index: cfiIndex })),
        },
    })

    it('uses the CFI when it resolves to the anchor chapter', () => {
        expect(resolveInitialDisplayTarget(bookWith(3), 'cfi-x', 3)).toBe('cfi-x')
    })

    it('falls back to the chapter when the CFI points somewhere else', () => {
        expect(resolveInitialDisplayTarget(bookWith(0), 'cfi-stale', 5)).toBe('ch5.xhtml')
    })

    it('uses the CFI when there is no chapter anchor to check it against', () => {
        expect(resolveInitialDisplayTarget(bookWith(0), 'cfi-x', null)).toBe('cfi-x')
    })

    it('keeps the CFI when the spine cannot resolve it (unprovable, not stale)', () => {
        expect(resolveInitialDisplayTarget(bookWith(null), 'cfi-x', 2)).toBe('cfi-x')
    })

    it('survives a spine.get that throws on a malformed CFI', () => {
        const book = bookWith(1)
        book.spine.get = vi.fn(() => { throw new Error('bad cfi') })
        expect(resolveInitialDisplayTarget(book, 'cfi-bad', 4)).toBe('cfi-bad')
    })

    it('uses the chapter when there is no CFI, and nothing when there is neither', () => {
        expect(resolveInitialDisplayTarget(bookWith(null), null, 2)).toBe('ch2.xhtml')
        expect(resolveInitialDisplayTarget(bookWith(null), null, null)).toBe(null)
        // Chapter index past the end of the spine is not navigable.
        expect(resolveInitialDisplayTarget(bookWith(null, 2), null, 9)).toBe(null)
    })
})
