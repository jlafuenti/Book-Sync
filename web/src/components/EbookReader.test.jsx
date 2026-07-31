import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import EbookReader, { resolveInitialDisplayTarget, executeRestore } from './EbookReader'

const {
    fetchEbookBlobMock, getPositionMock, updatePositionMock, matchTextToAudioMock,
    getDeviceIdMock, getDeviceNameMock, ePubMock,
} = vi.hoisted(() => ({
    fetchEbookBlobMock: vi.fn(),
    getPositionMock: vi.fn(),
    updatePositionMock: vi.fn(),
    matchTextToAudioMock: vi.fn(),
    getDeviceIdMock: vi.fn(() => 'device-abc'),
    getDeviceNameMock: vi.fn(() => 'Web · Chrome'),
    ePubMock: vi.fn(),
}))

vi.mock('../api', () => ({
    fetchEbookBlob: fetchEbookBlobMock,
    getPosition: getPositionMock,
    updatePosition: updatePositionMock,
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
    // Default: an unread book. That is the only state in which opening at the
    // start of the book is correct, and the only one where saving is allowed
    // straight away.
    getPositionMock.mockReset().mockResolvedValue(null)
    updatePositionMock.mockReset().mockResolvedValue({})
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

describe('EbookReader doSave — one atomic write', () => {
    // The save used to be two independent PUTs (progress + bookmark), each
    // adjudicated separately, so one could be accepted while the other was
    // rejected and the two records would then disagree about where the reader
    // was, permanently. It is now a single position write.

    it('writes the whole position once, with device attribution', async () => {
        await setupReader(LONG_TEXT)
        matchTextToAudioMock.mockResolvedValue(null)

        fireEvent.click(screen.getByTitle('Save position'))

        await waitFor(() => expect(updatePositionMock).toHaveBeenCalled())
        expect(updatePositionMock).toHaveBeenCalledTimes(1)
        expect(updatePositionMock).toHaveBeenCalledWith('pair', 42, expect.objectContaining({
            source: 'ebook',
            device_id: 'device-abc',
            device_name: 'Web · Chrome',
            captured_at: expect.any(String),
            hint: { kind: 'epubjs_cfi', value: 'cfi-test' },
        }))
    })

    it('upgrades the anchor to a sentence when the matcher hits', async () => {
        await setupReader(LONG_TEXT)
        matchTextToAudioMock.mockResolvedValue({
            epub_chapter: 3, epub_sentence_index: 11, audio_position_ms: 654321,
        })

        fireEvent.click(screen.getByTitle('Save position'))

        await waitFor(() => expect(updatePositionMock).toHaveBeenCalled())
        expect(updatePositionMock).toHaveBeenCalledWith('pair', 42, expect.objectContaining({
            epub_chapter: 3,
            epub_sentence_index: 11,
            audio_position_ms: 654321,
        }))
    })

    it('still writes a chapter + preview anchor when the matcher misses', async () => {
        // A matcher miss is not a failed save: the chapter and the text still
        // describe the position, and the other client can resolve from them.
        await setupReader(LONG_TEXT)
        matchTextToAudioMock.mockResolvedValue(null)

        fireEvent.click(screen.getByTitle('Save position'))

        await waitFor(() => expect(updatePositionMock).toHaveBeenCalled())
        const body = updatePositionMock.mock.calls[0][2]
        expect(body.epub_chapter).toBe(0)
        expect(body.epub_text_preview).toBeTruthy()
        expect(body.audio_position_ms).toBeUndefined()
    })

    it('skips the matcher when there is too little text, and still saves', async () => {
        await setupReader('short')
        fireEvent.click(screen.getByTitle('Save position'))

        await waitFor(() => expect(updatePositionMock).toHaveBeenCalled())
        expect(matchTextToAudioMock).not.toHaveBeenCalled()
    })

    it('surfaces a newer position from another device without navigating', async () => {
        const { rendition } = await setupReader(LONG_TEXT)
        matchTextToAudioMock.mockResolvedValue(null)
        updatePositionMock.mockResolvedValue({
            rejected: true,
            device_id: 'device-999',
            device_name: 'Phone',
            hints: [{ kind: 'epubjs_cfi', value: 'cfi-newer', current: true }],
        })

        const callsBefore = rendition.display.mock.calls.length
        fireEvent.click(screen.getByTitle('Save position'))
        await waitFor(() => expect(screen.getByText('Jump')).toBeInTheDocument())

        // Rendering the banner must never move the reader.
        expect(rendition.display.mock.calls.length).toBe(callsBefore)

        fireEvent.click(screen.getByText('Jump'))
        expect(rendition.display).toHaveBeenLastCalledWith('cfi-newer')
    })

    it('ignores a rejection that echoes this device (a retried write)', async () => {
        await setupReader(LONG_TEXT)
        matchTextToAudioMock.mockResolvedValue(null)
        updatePositionMock.mockResolvedValue({
            rejected: true, device_id: 'device-abc', device_name: 'Web · Chrome', hints: [],
        })

        fireEvent.click(screen.getByTitle('Save position'))
        await waitFor(() => expect(updatePositionMock).toHaveBeenCalled())

        expect(screen.queryByText(/Newer position available/)).not.toBeInTheDocument()
    })

    it('still saves a plain chapter + preview anchor when matchTextToAudio rejects outright', async () => {
        // A network error from the matcher is not a failed save either -- the
        // .catch() in doSave() must swallow it the same way a clean miss
        // (resolved null) is handled, falling back to the chapter anchor.
        await setupReader(LONG_TEXT)
        matchTextToAudioMock.mockRejectedValue(new Error('match service down'))

        fireEvent.click(screen.getByTitle('Save position'))

        await waitFor(() => expect(updatePositionMock).toHaveBeenCalled())
        const body = updatePositionMock.mock.calls[0][2]
        expect(body.epub_chapter).toBe(0)
        expect(body.epub_text_preview).toBeTruthy()
        expect(body.audio_position_ms).toBeUndefined()
    })
})

describe('EbookReader — a failed restore must not overwrite a real position', () => {
    // The regression that lost a real position: the reader could not resolve
    // the stored position, opened at page one, and the autosave persisted
    // chapter 0 over chapter 39.

    it('does not save when a stored position could not be resolved', async () => {
        // An anchor exists, but nothing in it can be resolved against this
        // book: the chapter is past the end of the spine, there is no hint,
        // no preview and no percent.
        getPositionMock.mockResolvedValue({
            anchor_revision: 5,
            epub_chapter: 900,
            hints: [],
        })
        const { book, rendition, handlers } = makeFakeBook(LONG_TEXT)
        ePubMock.mockReturnValue(book)
        render(
            <EbookReader
                ebookId={7} pairId={42}
                initialCfi={null} initialChapter={null} initialTextPreview={null}
                bookTitle="Test Book" onClose={vi.fn()}
            />
        )
        await waitFor(() => expect(rendition.display).toHaveBeenCalled())
        act(() => {
            handlers.relocated({
                start: { cfi: 'cfi-test', percentage: 0.01, displayed: { page: 1, total: 1 }, href: 'ch1.xhtml' },
            })
        })

        fireEvent.click(screen.getByTitle('Save position'))
        await new Promise(r => setTimeout(r, 50))

        expect(updatePositionMock).not.toHaveBeenCalled()
    })

    it('does save for a genuinely unread book', async () => {
        // The only case where opening at the start is correct.
        await setupReader(LONG_TEXT)
        matchTextToAudioMock.mockResolvedValue(null)

        fireEvent.click(screen.getByTitle('Save position'))

        await waitFor(() => expect(updatePositionMock).toHaveBeenCalled())
    })

    it('does not save when the only anchor (a percent) cannot be resolved on this book', async () => {
        // A record holding just an epub_progress_percent, with no hint/chapter/
        // preview/audio to fall back on. This book's fake `locations` has no
        // cfiFromPercentage, mirroring an epub.js book that hasn't generated
        // locations yet -- the percent step can't land, executeRestore must
        // return false, and doSave must stay shut just like the no-anchor case.
        getPositionMock.mockResolvedValue({
            anchor_revision: 2,
            epub_progress_percent: 45,
            hints: [],
        })
        const { book, rendition, handlers } = makeFakeBook(LONG_TEXT)
        ePubMock.mockReturnValue(book)
        render(
            <EbookReader
                ebookId={7} pairId={42}
                initialCfi={null} initialChapter={null} initialTextPreview={null}
                bookTitle="Test Book" onClose={vi.fn()}
            />
        )
        await waitFor(() => expect(rendition.display).toHaveBeenCalled())
        act(() => {
            handlers.relocated({
                start: { cfi: 'cfi-test', percentage: 0.45, displayed: { page: 1, total: 1 }, href: 'ch1.xhtml' },
            })
        })

        fireEvent.click(screen.getByTitle('Save position'))
        await new Promise(r => setTimeout(r, 50))

        expect(updatePositionMock).not.toHaveBeenCalled()
    })

    it('falls back to the props-supplied CFI when the position fetch fails (offline)', async () => {
        // loadBook()'s getPosition(...).catch() must synthesize a usable
        // fallback position from the initial* props rather than leaving the
        // reader with nothing to restore from.
        getPositionMock.mockRejectedValue(new Error('offline'))
        const { book, rendition } = makeFakeBook(LONG_TEXT)
        ePubMock.mockReturnValue(book)
        render(
            <EbookReader
                ebookId={7} pairId={42}
                initialCfi="cfi-prop" initialChapter={0} initialTextPreview="some preview text"
                bookTitle="Test Book" onClose={vi.fn()}
            />
        )

        await waitFor(() => expect(rendition.display).toHaveBeenCalledWith('cfi-prop'))
    })

    it('builds an empty hints array in the offline fallback when there is no initialCfi prop', async () => {
        // Same offline fallback, but only a chapter prop is available -- the
        // synthesized position must omit the epub_cfi hint entirely (`: []`)
        // rather than push a hint with an undefined value.
        getPositionMock.mockRejectedValue(new Error('offline'))
        const { book, rendition } = makeFakeBook(LONG_TEXT)
        ePubMock.mockReturnValue(book)
        render(
            <EbookReader
                ebookId={7} pairId={42}
                initialCfi={null} initialChapter={0} initialTextPreview={null}
                bookTitle="Test Book" onClose={vi.fn()}
            />
        )

        // No CFI hint to land on; the chapter step must be used instead.
        await waitFor(() => expect(rendition.display).toHaveBeenCalledWith('ch1.xhtml'))
    })

    it('falls back to null (open at start) when the fetch fails and there are no initial* props either', async () => {
        getPositionMock.mockRejectedValue(new Error('offline'))
        const { book, rendition } = makeFakeBook(LONG_TEXT)
        ePubMock.mockReturnValue(book)
        render(
            <EbookReader
                ebookId={7} pairId={42}
                initialCfi={null} initialChapter={null} initialTextPreview={null}
                bookTitle="Test Book" onClose={vi.fn()}
            />
        )

        // No anchor at all: executeRestore's "genuinely unread" path, calling
        // display() with no arguments.
        await waitFor(() => expect(rendition.display).toHaveBeenCalledWith())
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

describe('executeRestore — walks the ladder, landing on the first step that works', () => {
    // Direct unit tests against the exported function: cheaper and more
    // precise than driving the whole component through every ladder shape.

    it('lands on a chapter step, displaying the spine item at that index', async () => {
        const rendition = { display: vi.fn().mockResolvedValue(undefined) }
        const book = { spine: { items: [{ href: 'ch0.xhtml' }, { href: 'ch1.xhtml' }] } }

        const landed = await executeRestore(book, rendition, [{ kind: 'chapter', chapter: 1 }])

        expect(landed).toBe(true)
        expect(rendition.display).toHaveBeenCalledWith('ch1.xhtml')
    })

    it('skips a chapter step whose index has no spine item, falling through to the next step', async () => {
        const rendition = { display: vi.fn().mockResolvedValue(undefined) }
        const book = { spine: { items: [{ href: 'ch0.xhtml' }] } }

        const landed = await executeRestore(book, rendition, [
            { kind: 'chapter', chapter: 5 },
            { kind: 'hint', value: 'cfi-fallback' },
        ])

        expect(landed).toBe(true)
        expect(rendition.display).toHaveBeenCalledTimes(1)
        expect(rendition.display).toHaveBeenCalledWith('cfi-fallback')
    })

    it('lands on a percent step via book.locations.cfiFromPercentage', async () => {
        const rendition = { display: vi.fn().mockResolvedValue(undefined) }
        const book = {
            spine: { items: [] },
            locations: { cfiFromPercentage: vi.fn(() => 'cfi-at-40') },
        }

        const landed = await executeRestore(book, rendition, [{ kind: 'percent', percent: 40 }])

        expect(landed).toBe(true)
        expect(book.locations.cfiFromPercentage).toHaveBeenCalledWith(0.4)
        expect(rendition.display).toHaveBeenCalledWith('cfi-at-40')
    })

    it('catches a step that throws and falls through to the next one', async () => {
        const rendition = {
            display: vi.fn()
                .mockRejectedValueOnce(new Error('render exploded'))
                .mockResolvedValueOnce(undefined),
        }
        const book = { spine: { items: [] } }

        const landed = await executeRestore(book, rendition, [
            { kind: 'hint', value: 'cfi-bad' },
            { kind: 'hint', value: 'cfi-good' },
        ])

        expect(landed).toBe(true)
        expect(rendition.display).toHaveBeenNthCalledWith(1, 'cfi-bad')
        expect(rendition.display).toHaveBeenNthCalledWith(2, 'cfi-good')
    })

    it('opens at the start of book when there are no steps at all (genuinely unread)', async () => {
        const rendition = { display: vi.fn().mockResolvedValue(undefined) }
        const book = { spine: { items: [] } }

        const landed = await executeRestore(book, rendition, [])

        expect(landed).toBe(true)
        expect(rendition.display).toHaveBeenCalledWith()
    })

    it('returns false and still lands on start-of-book when every step fails to resolve', async () => {
        const rendition = { display: vi.fn().mockResolvedValue(undefined) }
        const book = { spine: { items: [{ href: 'ch0.xhtml' }] } }

        const landed = await executeRestore(book, rendition, [{ kind: 'chapter', chapter: 99 }])

        expect(landed).toBe(false)
        expect(rendition.display).toHaveBeenCalledWith()
    })
})

describe('EbookReader — initial text-nav pass', () => {
    it('does not leave saving suppressed when the preview is too short to search', async () => {
        // A preview under 5 normalized characters skips the search entirely
        // (the `if (shortTarget.length >= 5)` branch) and must still clear
        // textNavInProgressRef so the very next autosave is not swallowed by
        // doSave()'s "text nav in progress" guard.
        const { book, rendition, handlers } = makeFakeBook(LONG_TEXT)
        ePubMock.mockReturnValue(book)
        matchTextToAudioMock.mockResolvedValue(null)
        render(
            <EbookReader
                ebookId={7} pairId={42}
                initialCfi={null} initialChapter={null} initialTextPreview="hi"
                bookTitle="Test Book" onClose={vi.fn()}
            />
        )
        await waitFor(() => expect(rendition.display).toHaveBeenCalled())
        act(() => {
            handlers.relocated({
                start: { cfi: 'cfi-test', percentage: 0.5, displayed: { page: 1, total: 1 }, href: 'ch1.xhtml' },
            })
        })

        fireEvent.click(screen.getByTitle('Save position'))

        await waitFor(() => expect(updatePositionMock).toHaveBeenCalled())
    })
})
