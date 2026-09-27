import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import useReaderProgress, { PROGRESS_MODE_KEY, PAGE_MODE_KEY, SPEED_SAMPLES_KEY } from './useReaderProgress'

// Edge paths of the progress indicator's hook (issue #730); the main flows
// are pinned through the reader in EbookReader.progress.test.jsx.

const { getEbookMock, countSectionPagesMock, ePubMock } = vi.hoisted(() => ({
    getEbookMock: vi.fn(),
    countSectionPagesMock: vi.fn(),
    ePubMock: vi.fn(),
}))
vi.mock('../api', () => ({ getEbook: getEbookMock }))
vi.mock('epubjs', () => ({ default: ePubMock }))
vi.mock('../lib/pageCounter', async (importOriginal) => ({
    ...(await importOriginal()),
    countSectionPages: countSectionPagesMock,
}))

const COUNTED = { counts: [3, 5], chars: [3000, 5000] }

function viewer(width = 800, height = 600) {
    const el = document.createElement('div')
    const size = { width, height }
    Object.defineProperty(el, 'clientWidth', { get: () => size.width })
    Object.defineProperty(el, 'clientHeight', { get: () => size.height })
    return { ref: { current: el }, size }
}

function fakeBook(extra = {}) {
    const items = [{ href: 'ch0.xhtml' }, { href: 'ch1.xhtml' }]
    return { spine: { items, spineItems: items, get: vi.fn(() => null) }, ...extra }
}

// Renders the hook and opens `book` in it, as the reader does once the
// restore has landed.
function setup({ book = fakeBook(), ...overrides } = {}) {
    const v = viewer()
    const props = {
        ebookId: 7, fontSize: 100, ready: true, viewerRef: v.ref,
        bufferRef: { current: new ArrayBuffer(4) },
        ...overrides,
    }
    const hook = renderHook(p => useReaderProgress(p), { initialProps: props })
    if (book) act(() => hook.result.current.onOpened(book))
    return { ...hook, props, size: v.size, book }
}

function location(page, total = 5, cfi = 'cfi') {
    return { start: { cfi, location: 10, displayed: { page, total } } }
}

beforeEach(() => {
    localStorage.clear()
    getEbookMock.mockReset().mockResolvedValue({ print_page_count: null })
    countSectionPagesMock.mockReset()
    ePubMock.mockReset()
})
afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
})

describe('useReaderProgress — storage', () => {
    it('works with defaults when localStorage throws', () => {
        vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied') })
        vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('denied') })
        countSectionPagesMock.mockReturnValue(new Promise(() => {}))
        const { result } = setup()
        expect(result.current.mode).toBe('percent')
        expect(result.current.pageMode).toBe('ebook')
        act(() => result.current.cycle())
        expect(result.current.mode).toBe('pages')
        act(() => result.current.setPageMode('print'))
        expect(result.current.pageMode).toBe('print')
    })

    it('ignores stored samples that are not a JSON array', () => {
        countSectionPagesMock.mockReturnValue(new Promise(() => {}))
        for (const bad of ['not json', '{"a":1}']) {
            localStorage.setItem(SPEED_SAMPLES_KEY, bad)
            localStorage.setItem(PROGRESS_MODE_KEY, 'chapter')
            const { result, unmount } = setup()
            expect(result.current.mode).toBe('chapter')
            unmount()
        }
    })

    it('reads an unknown stored page mode as ebook pages', () => {
        countSectionPagesMock.mockReturnValue(new Promise(() => {}))
        localStorage.setItem(PAGE_MODE_KEY, 'bogus')
        const { result } = setup()
        expect(result.current.pageMode).toBe('ebook')
    })
})

describe('useReaderProgress — the page count', () => {
    it('recounts after the viewer is resized, debounced', async () => {
        let observed = null
        const disconnect = vi.fn()
        vi.stubGlobal('ResizeObserver', class {
            constructor(cb) { observed = cb }
            observe() {}
            disconnect() { disconnect() }
        })
        countSectionPagesMock.mockReturnValue(new Promise(() => {}))
        const { size, unmount } = setup()
        await waitFor(() => expect(countSectionPagesMock).toHaveBeenCalledTimes(1))
        const first = countSectionPagesMock.mock.calls[0][1]

        size.width = 1000
        act(() => { observed(); observed() })
        await act(async () => { await new Promise(r => setTimeout(r, 600)) })

        expect(countSectionPagesMock).toHaveBeenCalledTimes(2)
        expect(first.signal.aborted).toBe(true)
        expect(countSectionPagesMock.mock.calls[1][1]).toMatchObject({ width: 1000, height: 600 })
        unmount()
        expect(disconnect).toHaveBeenCalled()
    })

    it('a resize to the same size does not recount', async () => {
        let observed = null
        vi.stubGlobal('ResizeObserver', class {
            constructor(cb) { observed = cb }
            observe() {}
            disconnect() {}
        })
        countSectionPagesMock.mockReturnValue(new Promise(() => {}))
        setup()
        await waitFor(() => expect(countSectionPagesMock).toHaveBeenCalledTimes(1))
        act(() => observed())
        await act(async () => { await new Promise(r => setTimeout(r, 600)) })
        expect(countSectionPagesMock).toHaveBeenCalledTimes(1)
    })

    it('opens its own book from a copy of the fetched bytes', async () => {
        countSectionPagesMock.mockReturnValue(new Promise(() => {}))
        const { props } = setup()
        await waitFor(() => expect(countSectionPagesMock).toHaveBeenCalled())
        countSectionPagesMock.mock.calls[0][0]()
        const bytes = ePubMock.mock.calls[0][0]
        expect(bytes).toBeInstanceOf(ArrayBuffer)
        expect(bytes).not.toBe(props.bufferRef.current)
        expect(bytes.byteLength).toBe(4)
    })

    it('does not count before the book is ready, at zero size, or without a spine', async () => {
        countSectionPagesMock.mockReturnValue(new Promise(() => {}))
        setup({ ready: false })
        setup({ viewerRef: viewer(0, 0).ref })
        setup({ book: { spine: { items: [] } } })
        setup({ book: null })
        setup({ bufferRef: { current: null } })
        await act(async () => { await new Promise(r => setTimeout(r, 20)) })
        expect(countSectionPagesMock).not.toHaveBeenCalled()
    })

    it('does not count the previous book after the ebook changes under a mounted reader', async () => {
        countSectionPagesMock.mockReturnValue(new Promise(() => {}))
        const { result, rerender, props } = setup()
        await waitFor(() => expect(countSectionPagesMock).toHaveBeenCalledTimes(1))
        const first = countSectionPagesMock.mock.calls[0][1]

        rerender({ ...props, ebookId: 8 })
        await act(async () => { await new Promise(r => setTimeout(r, 20)) })
        expect(first.signal.aborted).toBe(true)
        expect(countSectionPagesMock).toHaveBeenCalledTimes(1)

        act(() => result.current.onOpened(fakeBook()))
        await waitFor(() => expect(countSectionPagesMock).toHaveBeenCalledTimes(2))
    })

    it("drops the previous book's numbers as soon as the ebook changes", async () => {
        countSectionPagesMock.mockResolvedValue(COUNTED)
        localStorage.setItem(PROGRESS_MODE_KEY, 'pages')
        const { result, rerender, props } = setup()
        await waitFor(() => expect(countSectionPagesMock).toHaveBeenCalled())
        act(() => result.current.onRelocated({ location: location(2), spineIndex: 1, fraction: 0.4 }))
        await waitFor(() => expect(result.current.text).toBe('5 of 8'))

        rerender({ ...props, ebookId: 8 })
        expect(result.current.text).toBe('…')
        act(() => result.current.cycle())   // chapter
        expect(result.current.text).toBe('…')
        act(() => result.current.cycle())   // time
        expect(result.current.text).toBe('…')
    })

    it('warns and stays pending when the count fails; an abort is silent', async () => {
        const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
        localStorage.setItem(PROGRESS_MODE_KEY, 'pages')
        countSectionPagesMock.mockRejectedValueOnce(new DOMException('Aborted', 'AbortError'))
        setup()
        await waitFor(() => expect(countSectionPagesMock).toHaveBeenCalledTimes(1))
        await act(async () => {})
        expect(warn).not.toHaveBeenCalled()

        countSectionPagesMock.mockRejectedValueOnce(new Error('layout exploded'))
        const { result } = setup({ ebookId: 8 })
        await waitFor(() => expect(warn).toHaveBeenCalledWith('[EbookReader] page count failed:', 'layout exploded'))
        expect(result.current.text).toBe('…')
    })

    it('a count that finishes after it was superseded is dropped', async () => {
        localStorage.setItem(PROGRESS_MODE_KEY, 'pages')
        let finishFirst
        countSectionPagesMock
            .mockReturnValueOnce(new Promise(r => { finishFirst = r }))
            .mockReturnValue(new Promise(() => {}))
        const { result, rerender, props } = setup()
        await waitFor(() => expect(countSectionPagesMock).toHaveBeenCalledTimes(1))
        rerender({ ...props, fontSize: 120 })
        await waitFor(() => expect(countSectionPagesMock).toHaveBeenCalledTimes(2))
        act(() => result.current.onRelocated({ location: location(2), spineIndex: 1, fraction: 0.4 }))

        await act(async () => { finishFirst(COUNTED) })
        expect(result.current.text).toBe('…')
        expect(localStorage.getItem('tandem_page_counts_v1')).toBeNull()
    })
})

describe('useReaderProgress — relocations', () => {
    it('a location outside the spine clears the previous section\'s print page', async () => {
        countSectionPagesMock.mockReturnValue(new Promise(() => {}))
        localStorage.setItem(PROGRESS_MODE_KEY, 'pages')
        localStorage.setItem(PAGE_MODE_KEY, 'print')
        const book = fakeBook({ pageList: { pageList: [
            { href: 'ch0.xhtml#p1', page: 1 },
            { href: 'ch1.xhtml#p2', page: 2 },
        ] } })
        const { result } = setup({ book })
        act(() => result.current.onRelocated({ location: location(1), spineIndex: 1, fraction: 0.5, book }))
        expect(result.current.text).toBe('1 of 2')
        act(() => result.current.onRelocated({ location: location(1), spineIndex: -1, fraction: 0.5, book }))
        expect(result.current.text).toBe('…')
    })

    it('a replaced file with the same spine is counted afresh', async () => {
        countSectionPagesMock.mockResolvedValue(COUNTED)
        const first = setup()
        await waitFor(() => expect(localStorage.getItem('tandem_page_counts_v1')).not.toBeNull())
        first.unmount()
        expect(countSectionPagesMock).toHaveBeenCalledTimes(1)

        // Same spine, same bytes: the cache answers.
        setup().unmount()
        await act(async () => {})
        expect(countSectionPagesMock).toHaveBeenCalledTimes(1)

        // Same spine, different bytes: counted again.
        setup({ bufferRef: { current: new ArrayBuffer(9) } })
        await waitFor(() => expect(countSectionPagesMock).toHaveBeenCalledTimes(2))
    })

    it('a location with no page, or outside the spine, records no dwell', async () => {
        countSectionPagesMock.mockResolvedValue(COUNTED)
        localStorage.setItem(PROGRESS_MODE_KEY, 'chapter')
        const { result } = setup()
        await waitFor(() => expect(countSectionPagesMock).toHaveBeenCalled())
        act(() => result.current.onRelocated({ location: { start: { cfi: 'x' } }, spineIndex: 1, fraction: 0 }))
        expect(result.current.text).toBe('…')
        act(() => result.current.onRelocated({ location: location(2), spineIndex: -1, fraction: 0 }))
        expect(result.current.text).toBe('2 of 5 in chapter')
        expect(localStorage.getItem(SPEED_SAMPLES_KEY)).toBeNull()
    })

    it('a page-list marker that cannot be placed counts as not reached', async () => {
        countSectionPagesMock.mockReturnValue(new Promise(() => {}))
        localStorage.setItem(PROGRESS_MODE_KEY, 'pages')
        localStorage.setItem(PAGE_MODE_KEY, 'print')
        const book = fakeBook({ pageList: { pageList: [
            { href: 'ch1.xhtml#p1', page: 1 },
            { href: 'ch1.xhtml#p2', page: 2 },
        ] } })
        const el = { id: 'p1' }
        const rendition = { getContents: () => [{ document: { getElementById: id => (id === 'p1' ? el : null) } }] }
        const { result } = setup({ book })
        // book.spine.get returns null, so building p1's CFI throws.
        act(() => result.current.onRelocated({ location: location(1), spineIndex: 1, fraction: 0.5, book, rendition }))
        expect(result.current.text).toBe('1 of 2')
    })

    it('treats an ebook record without a print page count as none', async () => {
        getEbookMock.mockResolvedValue(null)
        countSectionPagesMock.mockResolvedValue(COUNTED)
        localStorage.setItem(PROGRESS_MODE_KEY, 'pages')
        localStorage.setItem(PAGE_MODE_KEY, 'print')
        const { result } = setup()
        await waitFor(() => expect(countSectionPagesMock).toHaveBeenCalled())
        act(() => result.current.onRelocated({ location: location(2), spineIndex: 1, fraction: 0.4 }))
        await waitFor(() => expect(result.current.text).toBe('5 of 8'))
        expect(result.current.fallback).toBe(true)
    })
})
