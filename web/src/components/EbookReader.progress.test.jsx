import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import EbookReader from './EbookReader'
import { cacheKey, writeCounts } from '../lib/pageCountCache'
import { spineSignature } from '../lib/pageCounter'
import { setViewport } from '../test/setup'

// The reader progress indicator (issue #730): tap to cycle percent → pages →
// chapter → time, with the page counter and print-page count mocked.

const {
    fetchEbookBlobMock, getPositionMock, updatePositionMock, matchTextToAudioMock,
    getDeviceIdMock, getDeviceNameMock, ePubMock, sendPositionKeepaliveMock, audioToEpubMock,
    getEbookMock, countSectionPagesMock,
} = vi.hoisted(() => ({
    fetchEbookBlobMock: vi.fn(),
    getPositionMock: vi.fn(),
    updatePositionMock: vi.fn(),
    matchTextToAudioMock: vi.fn(),
    getDeviceIdMock: vi.fn(() => 'device-abc'),
    getDeviceNameMock: vi.fn(() => 'Web · Chrome'),
    ePubMock: vi.fn(),
    sendPositionKeepaliveMock: vi.fn(),
    audioToEpubMock: vi.fn(),
    getEbookMock: vi.fn(),
    countSectionPagesMock: vi.fn(),
}))

vi.mock('../api', () => ({
    fetchEbookBlob: fetchEbookBlobMock,
    getPosition: getPositionMock,
    updatePosition: updatePositionMock,
    matchTextToAudio: matchTextToAudioMock,
    getDeviceId: getDeviceIdMock,
    getDeviceName: getDeviceNameMock,
    sendPositionKeepalive: sendPositionKeepaliveMock,
    audioToEpub: audioToEpubMock,
    getEbook: getEbookMock,
}))
vi.mock('epubjs', () => ({ default: ePubMock }))
vi.mock('../lib/pageCounter', async (importOriginal) => ({
    ...(await importOriginal()),
    countSectionPages: countSectionPagesMock,
}))

const COUNTED = { counts: [3, 5], chars: [3000, 5000] }
const HREFS = ['ch0.xhtml', 'ch1.xhtml']

function makeFakeBook() {
    const handlers = {}
    const doc = {
        body: { innerText: 'The quick brown fox jumps over the lazy dog and keeps on running.' },
        head: { appendChild: vi.fn() },
        createElement: vi.fn(() => ({ id: '', textContent: '' })),
        getElementById: vi.fn(() => null),
    }
    const rendition = {
        themes: { default: vi.fn() },
        hooks: { content: { register: vi.fn() } },
        on: vi.fn((event, cb) => { handlers[event] = cb }),
        display: vi.fn().mockResolvedValue(undefined),
        getContents: vi.fn(() => [{ document: doc }]),
        reportLocation: vi.fn(),
        prev: vi.fn(),
        next: vi.fn(),
    }
    const items = HREFS.map(href => ({ href }))
    const book = {
        renderTo: vi.fn(() => rendition),
        loaded: { navigation: Promise.resolve({ toc: [] }) },
        spine: { items, spineItems: items, get: vi.fn(() => null) },
        locations: { generate: vi.fn().mockResolvedValue(undefined) },
        ready: Promise.resolve(),
        destroy: vi.fn(),
    }
    return { book, rendition, handlers }
}

function deferred() {
    let resolve, reject
    const promise = new Promise((res, rej) => { resolve = res; reject = rej })
    return { promise, resolve, reject }
}

let now = 1_000_000
beforeEach(() => {
    localStorage.clear()
    fetchEbookBlobMock.mockReset().mockResolvedValue(new ArrayBuffer(0))
    getPositionMock.mockReset().mockResolvedValue(null)
    updatePositionMock.mockReset().mockResolvedValue({})
    matchTextToAudioMock.mockReset().mockResolvedValue(null)
    getDeviceIdMock.mockReset().mockReturnValue('device-abc')
    getDeviceNameMock.mockReset().mockReturnValue('Web · Chrome')
    ePubMock.mockReset()
    sendPositionKeepaliveMock.mockReset()
    audioToEpubMock.mockReset().mockResolvedValue(null)
    getEbookMock.mockReset().mockResolvedValue({ print_page_count: null })
    countSectionPagesMock.mockReset()
    now = 1_000_000
    vi.spyOn(Date, 'now').mockImplementation(() => now)
    // jsdom lays nothing out; give the viewer a size so the count can start.
    // (The real getters live on Element.prototype; these shadow them.)
    for (const [prop, value] of [['clientWidth', 800], ['clientHeight', 600]]) {
        Object.defineProperty(HTMLElement.prototype, prop, { configurable: true, get: () => value })
    }
})
afterEach(() => {
    vi.restoreAllMocks()
    delete HTMLElement.prototype.clientWidth
    delete HTMLElement.prototype.clientHeight
})

async function openReader(props = {}) {
    const fake = makeFakeBook()
    ePubMock.mockReturnValue(fake.book)
    const utils = render(
        <EbookReader
            ebookId={7} pairId={42}
            initialChapter={null} initialTextPreview={null}
            bookTitle="Axis Test" onClose={vi.fn()}
            {...props}
        />
    )
    await waitFor(() => expect(fake.handlers.relocated).toBeDefined())
    return { ...fake, ...utils }
}

// A located epub.js location. `location` is the locations index: epub.js
// 0.3.93 reports -1 (and a percentage of 0) until `book.locations` has been
// generated, then a real index.
function locatedAt({ section, page, total = 5, percentage = 0.415, location = 10 }) {
    return {
        start: {
            cfi: `cfi-${section}-${page}`, percentage, location,
            displayed: { page, total }, href: HREFS[section],
        },
    }
}

function relocate(handlers, at) {
    act(() => { handlers.relocated(locatedAt(at)) })
}

const indicator = () => screen.getByRole('button', { name: /^Reading progress/ })

describe('EbookReader — the progress indicator cycles its modes (issue #730)', () => {
    it('shows the percent, then pages: pending while counting, then the book-wide page', async () => {
        const count = deferred()
        countSectionPagesMock.mockReturnValue(count.promise)
        const { handlers } = await openReader()
        relocate(handlers, { section: 1, page: 2 })

        expect(indicator()).toHaveTextContent('41.5%')
        fireEvent.click(indicator())
        expect(indicator()).toHaveTextContent('…')

        await waitFor(() => expect(countSectionPagesMock).toHaveBeenCalled())
        await act(async () => { count.resolve(COUNTED) })
        await waitFor(() => expect(indicator()).toHaveTextContent('5 of 8'))
    })

    it('then the page in the chapter, then the time left in it, then back to percent', async () => {
        countSectionPagesMock.mockResolvedValue(COUNTED)
        const { handlers } = await openReader()
        relocate(handlers, { section: 1, page: 2 })
        await waitFor(() => expect(countSectionPagesMock).toHaveBeenCalled())

        fireEvent.click(indicator())
        await waitFor(() => expect(indicator()).toHaveTextContent('5 of 8'))
        fireEvent.click(indicator())
        expect(indicator()).toHaveTextContent('2 of 5 in chapter')
        fireEvent.click(indicator())
        // 5000 chars at the default 25 cps over pages 2–5 of 5 = 160 s.
        expect(indicator()).toHaveTextContent('3 min left in chapter')
        fireEvent.click(indicator())
        expect(indicator()).toHaveTextContent('41.5%')
    })

    it('shows … for the time left until the chapter has been counted', async () => {
        countSectionPagesMock.mockReturnValue(new Promise(() => {}))
        localStorage.setItem('tandem_reader_progress_mode', 'time')
        const { handlers } = await openReader()
        relocate(handlers, { section: 1, page: 2 })
        expect(indicator()).toHaveTextContent('…')
    })

    it('its accessible name carries the value shown, in every mode', async () => {
        countSectionPagesMock.mockResolvedValue(COUNTED)
        const { handlers } = await openReader()
        relocate(handlers, { section: 1, page: 2 })
        await waitFor(() => expect(countSectionPagesMock).toHaveBeenCalled())

        expect(indicator()).toHaveAccessibleName('Reading progress: 41.5%, tap to change')
        fireEvent.click(indicator())
        await waitFor(() => expect(indicator()).toHaveAccessibleName('Reading progress: 5 of 8, tap to change'))
        fireEvent.click(indicator())
        expect(indicator()).toHaveAccessibleName('Reading progress: 2 of 5 in chapter, tap to change')
        fireEvent.click(indicator())
        expect(indicator()).toHaveAccessibleName('Reading progress: 3 min left in chapter, tap to change')
    })

    it('keeps the chosen mode across a remount', async () => {
        countSectionPagesMock.mockResolvedValue(COUNTED)
        const first = await openReader()
        fireEvent.click(indicator())
        expect(localStorage.getItem('tandem_reader_progress_mode')).toBe('pages')
        first.unmount()

        const { handlers } = await openReader()
        relocate(handlers, { section: 1, page: 2 })
        await waitFor(() => expect(indicator()).toHaveTextContent('5 of 8'))
    })
})

describe('EbookReader — print pages (issue #730)', () => {
    it("uses the ebook's print page count, loaded once for the ebook", async () => {
        getEbookMock.mockResolvedValue({ print_page_count: 300 })
        countSectionPagesMock.mockResolvedValue(COUNTED)
        localStorage.setItem('tandem_reader_progress_mode', 'pages')
        localStorage.setItem('tandem_reader_page_mode', 'print')
        const { handlers } = await openReader()
        relocate(handlers, { section: 1, page: 2, percentage: 0.4 })

        await waitFor(() => expect(indicator()).toHaveTextContent('120 of 300'))
        expect(getEbookMock).toHaveBeenCalledTimes(1)
        expect(getEbookMock).toHaveBeenCalledWith(7)
    })

    it('print pages scaled from the count wait for the book-wide position', async () => {
        getEbookMock.mockResolvedValue({ print_page_count: 300 })
        countSectionPagesMock.mockResolvedValue(COUNTED)
        localStorage.setItem('tandem_reader_progress_mode', 'pages')
        localStorage.setItem('tandem_reader_page_mode', 'print')
        const { handlers } = await openReader()
        await waitFor(() => expect(getEbookMock).toHaveBeenCalled())
        await waitFor(() => expect(countSectionPagesMock).toHaveBeenCalled())
        // Before epub.js has generated its locations: location -1, percentage 0.
        relocate(handlers, { section: 1, page: 2, percentage: 0, location: -1 })
        await act(async () => { await new Promise(r => setTimeout(r, 20)) })
        expect(indicator()).toHaveTextContent('…')
        expect(indicator()).not.toHaveTextContent('1 of 300')

        // locations.generate → reportLocation re-reports with a real location.
        relocate(handlers, { section: 1, page: 2, percentage: 0.4, location: 120 })
        await waitFor(() => expect(indicator()).toHaveTextContent('120 of 300'))
    })

    it('falls back to ebook pages, marked, when there is no count and no page list', async () => {
        countSectionPagesMock.mockResolvedValue(COUNTED)
        localStorage.setItem('tandem_reader_progress_mode', 'pages')
        localStorage.setItem('tandem_reader_page_mode', 'print')
        const { handlers } = await openReader()
        relocate(handlers, { section: 1, page: 2, percentage: 0.4 })

        await waitFor(() => expect(indicator()).toHaveTextContent('5 of 8'))
        expect(indicator()).toHaveAccessibleName('Reading progress: 5 of 8 (ebook pages), tap to change')
        const marker = indicator().querySelector('sup')
        expect(marker).toHaveTextContent('e')
        expect(marker).toHaveAttribute('aria-hidden', 'true')
        expect(marker).not.toHaveAttribute('aria-label')
    })

    it('treats a failed ebook fetch as no print page count', async () => {
        getEbookMock.mockRejectedValue(new Error('offline'))
        countSectionPagesMock.mockResolvedValue(COUNTED)
        localStorage.setItem('tandem_reader_progress_mode', 'pages')
        localStorage.setItem('tandem_reader_page_mode', 'print')
        const { handlers } = await openReader()
        relocate(handlers, { section: 1, page: 2, percentage: 0.4 })

        await waitFor(() => expect(indicator()).toHaveTextContent('5 of 8'))
        expect(indicator()).toHaveAccessibleName(/\(ebook pages\)/)
    })

    it('uses the embedded page list when the book has one', async () => {
        countSectionPagesMock.mockResolvedValue(COUNTED)
        localStorage.setItem('tandem_reader_progress_mode', 'pages')
        localStorage.setItem('tandem_reader_page_mode', 'print')
        const fake = makeFakeBook()
        fake.book.pageList = { pageList: [
            { href: 'ch0.xhtml#p1', page: 1 },
            { href: 'ch1.xhtml#p2', page: 2 },
            { href: 'ch1.xhtml#p3', page: 3 },
        ] }
        ePubMock.mockReturnValue(fake.book)
        render(
            <EbookReader ebookId={7} pairId={42} initialChapter={null}
                initialTextPreview={null} bookTitle="Axis Test" onClose={vi.fn()} />
        )
        await waitFor(() => expect(fake.handlers.relocated).toBeDefined())
        relocate(fake.handlers, { section: 1, page: 1 })
        // Neither marker in section 1 is rendered in the fake document, so the
        // last one at or before the position is page 1, in section 0.
        await waitFor(() => expect(indicator()).toHaveTextContent('1 of 3'))
        expect(indicator().querySelector('sup')).toBeNull()
        expect(indicator()).toHaveAccessibleName('Reading progress: 1 of 3, tap to change')
    })

    it('the page-number setting switches between ebook and print pages and is saved', async () => {
        getEbookMock.mockResolvedValue({ print_page_count: 300 })
        countSectionPagesMock.mockResolvedValue(COUNTED)
        localStorage.setItem('tandem_reader_progress_mode', 'pages')
        const { handlers } = await openReader()
        relocate(handlers, { section: 1, page: 2, percentage: 0.4 })
        await waitFor(() => expect(indicator()).toHaveTextContent('5 of 8'))

        const select = screen.getByLabelText('Page numbers')
        expect(select).toHaveValue('ebook')
        fireEvent.change(select, { target: { value: 'print' } })
        await waitFor(() => expect(indicator()).toHaveTextContent('120 of 300'))
        expect(localStorage.getItem('tandem_reader_page_mode')).toBe('print')
    })
})

describe('EbookReader — the page count (issue #730)', () => {
    it('a font size change aborts the running count and starts one at the new size', async () => {
        countSectionPagesMock.mockReturnValue(new Promise(() => {}))
        await openReader()
        await waitFor(() => expect(countSectionPagesMock).toHaveBeenCalledTimes(1))
        const [openBook, firstOpts] = countSectionPagesMock.mock.calls[0]
        expect(typeof openBook).toBe('function')
        expect(firstOpts).toMatchObject({ width: 800, height: 600, fontSize: 100 })
        expect(firstOpts.signal.aborted).toBe(false)

        fireEvent.click(screen.getByTitle('Increase font'))
        await waitFor(() => expect(countSectionPagesMock).toHaveBeenCalledTimes(2))
        expect(firstOpts.signal.aborted).toBe(true)
        expect(countSectionPagesMock.mock.calls[1][1]).toMatchObject({ fontSize: 110 })
    })

    it('uses a cached count without counting again', async () => {
        const fake = makeFakeBook()
        const key = cacheKey({ ebookId: 7, signature: `${spineSignature(fake.book)}:0`, width: 800, height: 600, fontSize: 100 })
        writeCounts(key, COUNTED)
        localStorage.setItem('tandem_reader_progress_mode', 'pages')
        const { handlers } = await openReader()
        relocate(handlers, { section: 1, page: 2 })

        await waitFor(() => expect(indicator()).toHaveTextContent('5 of 8'))
        expect(countSectionPagesMock).not.toHaveBeenCalled()
    })

    it('caches a finished count under the viewer size and font size', async () => {
        const fake = makeFakeBook()
        const key = cacheKey({ ebookId: 7, signature: `${spineSignature(fake.book)}:0`, width: 800, height: 600, fontSize: 100 })
        countSectionPagesMock.mockResolvedValue(COUNTED)
        await openReader()
        await waitFor(() => expect(JSON.parse(localStorage.getItem('tandem_page_counts_v1'))[key].v).toEqual(COUNTED))
    })
})

describe('EbookReader — reading speed samples (issue #730)', () => {
    const samples = () => JSON.parse(localStorage.getItem('tandem_reader_speed_samples') || '[]')

    async function counted() {
        countSectionPagesMock.mockResolvedValue(COUNTED)
        const fake = await openReader()
        await waitFor(() => expect(countSectionPagesMock).toHaveBeenCalled())
        // Wait for the counts to land: the pages label stops being pending.
        fireEvent.click(indicator())
        relocate(fake.handlers, { section: 1, page: 2 })
        await waitFor(() => expect(indicator()).toHaveTextContent('5 of 8'))
        return fake
    }

    it('a forward page turn after 60 s records the page\'s characters per second', async () => {
        const { handlers } = await counted()
        now += 60_000
        relocate(handlers, { section: 1, page: 3 })
        expect(samples()).toHaveLength(1)
        expect(samples()[0]).toBeCloseTo(5000 / 5 / 60)
    })

    it('a forward turn into the next chapter counts the page left behind', async () => {
        const { handlers } = await counted()
        relocate(handlers, { section: 0, page: 3, total: 3 })
        now += 60_000
        relocate(handlers, { section: 1, page: 1 })
        expect(samples()).toHaveLength(1)
        expect(samples()[0]).toBeCloseTo(3000 / 3 / 60)
    })

    it('a backward turn, a jump, and a turn after 1 s record nothing', async () => {
        const { handlers } = await counted()
        now += 60_000
        relocate(handlers, { section: 1, page: 1 })              // backward
        now += 60_000
        relocate(handlers, { section: 1, page: 3 })              // jump of two pages
        now += 60_000
        relocate(handlers, { section: 0, page: 1, total: 3 })    // across sections, backward
        now += 60_000
        relocate(handlers, { section: 1, page: 3 })              // into the next section, not page 1
        now += 1_000
        relocate(handlers, { section: 1, page: 4 })              // too quick
        expect(samples()).toEqual([])
    })

    it('a re-report of the same page does not restart the dwell clock', async () => {
        const { handlers } = await counted()
        now += 40_000
        relocate(handlers, { section: 1, page: 2 })
        now += 20_000
        relocate(handlers, { section: 1, page: 3 })
        expect(samples()).toHaveLength(1)
        expect(samples()[0]).toBeCloseTo(5000 / 5 / 60)
    })

    it('records nothing before the chapter has been counted', async () => {
        countSectionPagesMock.mockReturnValue(new Promise(() => {}))
        const { handlers } = await openReader()
        relocate(handlers, { section: 1, page: 2 })
        now += 60_000
        relocate(handlers, { section: 1, page: 3 })
        expect(samples()).toEqual([])
    })
})

describe('EbookReader — the indicator on open, the keyboard, and phones (issue #730)', () => {
    afterEach(() => setViewport(1200))

    it('shows where the restore landed before any page turn', async () => {
        countSectionPagesMock.mockReturnValue(new Promise(() => {}))
        localStorage.setItem('tandem_reader_progress_mode', 'chapter')
        const fake = makeFakeBook()
        fake.rendition.currentLocation = vi.fn(() => locatedAt({ section: 1, page: 2 }))
        ePubMock.mockReturnValue(fake.book)
        render(
            <EbookReader ebookId={7} pairId={42} initialChapter={null}
                initialTextPreview={null} bookTitle="Axis Test" onClose={vi.fn()} />
        )
        await waitFor(() => expect(fake.handlers.relocated).toBeDefined())
        expect(indicator()).toHaveTextContent('2 of 5 in chapter')
    })

    it('a landing with no href still seeds the page in the chapter', async () => {
        countSectionPagesMock.mockReturnValue(new Promise(() => {}))
        localStorage.setItem('tandem_reader_progress_mode', 'chapter')
        const fake = makeFakeBook()
        fake.rendition.currentLocation = vi.fn(() => ({ start: { cfi: 'cfi-x', displayed: { page: 3, total: 4 } } }))
        ePubMock.mockReturnValue(fake.book)
        render(
            <EbookReader ebookId={7} pairId={42} initialChapter={null}
                initialTextPreview={null} bookTitle="Axis Test" onClose={vi.fn()} />
        )
        await waitFor(() => expect(fake.handlers.relocated).toBeDefined())
        expect(indicator()).toHaveTextContent('3 of 4 in chapter')
    })

    it('a rendition whose currentLocation throws or has nothing leaves the indicator pending', async () => {
        countSectionPagesMock.mockReturnValue(new Promise(() => {}))
        localStorage.setItem('tandem_reader_progress_mode', 'chapter')
        for (const currentLocation of [() => { throw new Error('no manager') }, () => undefined]) {
            const fake = makeFakeBook()
            fake.rendition.currentLocation = vi.fn(currentLocation)
            ePubMock.mockReturnValue(fake.book)
            const { unmount } = render(
                <EbookReader ebookId={7} pairId={42} initialChapter={null}
                    initialTextPreview={null} bookTitle="Axis Test" onClose={vi.fn()} />
            )
            await waitFor(() => expect(fake.handlers.relocated).toBeDefined())
            expect(indicator()).toHaveTextContent('…')
            unmount()
        }
    })

    it('keys pressed in the page-number select or on the indicator do not turn pages', async () => {
        countSectionPagesMock.mockReturnValue(new Promise(() => {}))
        const { rendition } = await openReader()
        fireEvent.keyDown(screen.getByLabelText('Page numbers'), { key: 'ArrowRight' })
        fireEvent.keyDown(screen.getByLabelText('Page numbers'), { key: 'ArrowLeft' })
        fireEvent.keyDown(indicator(), { key: ' ' })
        expect(rendition.next).not.toHaveBeenCalled()
        expect(rendition.prev).not.toHaveBeenCalled()

        fireEvent.keyDown(document.body, { key: 'ArrowRight' })
        expect(rendition.next).toHaveBeenCalledTimes(1)
    })

    it('Escape in the select still closes the reader', async () => {
        countSectionPagesMock.mockReturnValue(new Promise(() => {}))
        const onClose = vi.fn()
        await openReader({ onClose })
        fireEvent.keyDown(screen.getByLabelText('Page numbers'), { key: 'Escape' })
        expect(onClose).toHaveBeenCalled()
    })

    it('on a phone the page-number select lives in the reader-theme menu', async () => {
        setViewport(375)
        countSectionPagesMock.mockReturnValue(new Promise(() => {}))
        const { container } = await openReader()
        expect(screen.queryByLabelText('Page numbers')).not.toBeInTheDocument()

        fireEvent.click(screen.getByTitle('Reader theme'))
        const select = screen.getByLabelText('Page numbers')
        expect(container.querySelector('.reader-theme-menu')).toContainElement(select)
        fireEvent.change(select, { target: { value: 'print' } })
        expect(localStorage.getItem('tandem_reader_page_mode')).toBe('print')
    })

    it('on a wide screen it stays in the toolbar beside the font buttons', async () => {
        countSectionPagesMock.mockReturnValue(new Promise(() => {}))
        const { container } = await openReader()
        expect(container.querySelector('.font-size-controls')).toContainElement(screen.getByLabelText('Page numbers'))
        fireEvent.click(screen.getByTitle('Reader theme'))
        expect(screen.getAllByLabelText('Page numbers')).toHaveLength(1)
    })
})
