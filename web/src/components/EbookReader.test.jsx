import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import EbookReader from './EbookReader'

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
