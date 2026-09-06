import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import useEpubRendition from './useEpubRendition'

const { fetchEbookBlobMock, ePubMock } = vi.hoisted(() => ({
    fetchEbookBlobMock: vi.fn(),
    ePubMock: vi.fn(),
}))

vi.mock('../api', () => ({ fetchEbookBlob: fetchEbookBlobMock }))
vi.mock('epubjs', () => ({ default: ePubMock }))

// The same lean epub.js double EbookReader.test.jsx uses: only the surface the
// rendition seam touches (renderTo / themes / hooks / loaded.navigation /
// ready / locations / destroy).
function makeFakeBook({ toc = [{ href: 'ch1.xhtml', label: 'One' }] } = {}) {
    const rendition = {
        themes: { default: vi.fn() },
        hooks: { content: { register: vi.fn() } },
        reportLocation: vi.fn(),
    }
    let resolveReady
    const book = {
        renderTo: vi.fn(() => rendition),
        loaded: { navigation: Promise.resolve({ toc }) },
        spine: { items: [{ href: 'ch1.xhtml' }] },
        locations: { generate: vi.fn().mockResolvedValue(undefined) },
        ready: new Promise(r => { resolveReady = r }),
        destroy: vi.fn(),
    }
    return { book, rendition, resolveReady }
}

function makeContents() {
    const appended = []
    return {
        appended,
        document: {
            createElement: vi.fn(() => ({ id: '', textContent: '' })),
            head: { appendChild: vi.fn(el => appended.push(el)) },
        },
    }
}

const BUFFER = new ArrayBuffer(8)

beforeEach(() => {
    fetchEbookBlobMock.mockReset().mockResolvedValue(BUFFER)
    ePubMock.mockReset()
})

function renderRendition(ebookId, opts = {}) {
    const viewerRef = { current: document.createElement('div') }
    const fontSizeRef = { current: 100 }
    const paletteRef = { current: { background: '#fff', text: '#111', link: '#00f' } }
    const onOpened = opts.onOpened ?? vi.fn().mockResolvedValue(undefined)
    const onTeardown = opts.onTeardown ?? vi.fn()
    const hook = renderHook(
        ({ id }) => useEpubRendition(viewerRef, id, { fontSizeRef, paletteRef, onOpened, onTeardown }),
        { initialProps: { id: ebookId } },
    )
    return { ...hook, viewerRef, fontSizeRef, paletteRef, onOpened, onTeardown }
}

describe('useEpubRendition — opening', () => {
    it('fetches the blob, builds the book and renders it paginated into the viewer', async () => {
        const { book, rendition } = makeFakeBook()
        ePubMock.mockReturnValue(book)

        const { result, viewerRef } = renderRendition(7)
        expect(result.current.loading).toBe(true)

        await waitFor(() => expect(result.current.loading).toBe(false))
        expect(fetchEbookBlobMock).toHaveBeenCalledWith(7)
        expect(ePubMock).toHaveBeenCalledWith(BUFFER)
        expect(book.renderTo).toHaveBeenCalledWith(viewerRef.current, {
            width: '100%', height: '100%', flow: 'paginated', spread: 'none',
        })
        expect(result.current.bookRef.current).toBe(book)
        expect(result.current.renditionRef.current).toBe(rendition)
        expect(result.current.error).toBeNull()
    })

    it('applies the structural default theme (colors come from the injected style, not here)', async () => {
        const { book, rendition } = makeFakeBook()
        ePubMock.mockReturnValue(book)

        const { result } = renderRendition(7)
        await waitFor(() => expect(result.current.loading).toBe(false))

        const [theme] = rendition.themes.default.mock.calls[0]
        expect(theme.body['font-family']).toContain('Georgia')
        expect(theme.body.padding).toBe('0 48px !important')
        expect(theme.body.background).toBeUndefined()
        expect(theme.body.color).toBeUndefined()
    })

    it('injects font size and palette <style>s per chapter document, reading the refs at hook time', async () => {
        const { book, rendition } = makeFakeBook()
        ePubMock.mockReturnValue(book)

        const { result, fontSizeRef, paletteRef } = renderRendition(7)
        await waitFor(() => expect(result.current.loading).toBe(false))
        const [hook] = rendition.hooks.content.register.mock.calls[0]

        // A pref change AFTER registration must still reach the next chapter.
        fontSizeRef.current = 130
        paletteRef.current = { background: '#000', text: '#eee', link: '#9cf' }
        const contents = makeContents()
        hook(contents)

        expect(contents.appended.map(el => el.id)).toEqual(['tandem-font-size', 'tandem-reader-theme'])
        expect(contents.appended[0].textContent).toBe('html { font-size: 130% !important; }')
        expect(contents.appended[1].textContent).toContain('background: #000 !important')
        expect(contents.appended[1].textContent).toContain('color: #eee !important')
        expect(contents.appended[1].textContent).toContain('a { color: #9cf !important; }')
    })

    it('ignores contents without a document (nothing to style)', async () => {
        const { book, rendition } = makeFakeBook()
        ePubMock.mockReturnValue(book)
        const { result } = renderRendition(7)
        await waitFor(() => expect(result.current.loading).toBe(false))
        const [hook] = rendition.hooks.content.register.mock.calls[0]

        expect(() => hook({})).not.toThrow()
    })

    it('exposes the navigation TOC, defaulting to an empty list', async () => {
        const { book } = makeFakeBook({ toc: [{ href: 'a.xhtml', label: 'A' }] })
        ePubMock.mockReturnValue(book)
        const { result } = renderRendition(7)
        await waitFor(() => expect(result.current.toc).toEqual([{ href: 'a.xhtml', label: 'A' }]))

        const { book: bare } = makeFakeBook({ toc: null })
        ePubMock.mockReturnValue(bare)
        const { result: r2 } = renderRendition(8)
        await waitFor(() => expect(r2.current.loading).toBe(false))
        expect(r2.current.toc).toEqual([])
    })
})

describe('useEpubRendition — the onOpened handoff', () => {
    // The restore (and the relocated handler it precedes) must run in the
    // same async continuation as the open, after the TOC and before the
    // spinner clears — that ordering is what EbookReader.test.jsx pins from
    // the outside.

    it('calls onOpened with the book, rendition, nav and an isDestroyed probe once the TOC is loaded', async () => {
        const { book, rendition } = makeFakeBook()
        ePubMock.mockReturnValue(book)
        const { result, onOpened } = renderRendition(7)
        await waitFor(() => expect(result.current.loading).toBe(false))

        expect(onOpened).toHaveBeenCalledTimes(1)
        const [arg] = onOpened.mock.calls[0]
        expect(arg.book).toBe(book)
        expect(arg.rendition).toBe(rendition)
        expect(arg.nav.toc).toEqual([{ href: 'ch1.xhtml', label: 'One' }])
        expect(arg.isDestroyed()).toBe(false)
    })

    it('keeps loading=true until onOpened resolves', async () => {
        const { book } = makeFakeBook()
        ePubMock.mockReturnValue(book)
        let finish
        const onOpened = vi.fn(() => new Promise(r => { finish = r }))
        const { result } = renderRendition(7, { onOpened })

        await waitFor(() => expect(onOpened).toHaveBeenCalled())
        expect(result.current.loading).toBe(true)
        expect(book.locations.generate).not.toHaveBeenCalled()

        await act(async () => { finish() })
        await waitFor(() => expect(result.current.loading).toBe(false))
    })

    it('generates locations and re-reports the location only after the handoff', async () => {
        const { book, rendition, resolveReady } = makeFakeBook()
        ePubMock.mockReturnValue(book)
        const { result } = renderRendition(7)
        await waitFor(() => expect(result.current.loading).toBe(false))

        expect(book.locations.generate).not.toHaveBeenCalled()
        await act(async () => { resolveReady() })
        await waitFor(() => expect(book.locations.generate).toHaveBeenCalledWith(1024))
        await waitFor(() => expect(rendition.reportLocation).toHaveBeenCalledTimes(1))
    })

    it('reads the latest onOpened without restarting the open', async () => {
        const { book } = makeFakeBook()
        ePubMock.mockReturnValue(book)
        const viewerRef = { current: document.createElement('div') }
        const refs = { fontSizeRef: { current: 100 }, paletteRef: { current: {} } }
        const first = vi.fn().mockResolvedValue(undefined)
        const second = vi.fn().mockResolvedValue(undefined)
        const { rerender, result } = renderHook(
            ({ cb }) => useEpubRendition(viewerRef, 7, { ...refs, onOpened: cb }),
            { initialProps: { cb: first } },
        )
        rerender({ cb: second })
        await waitFor(() => expect(result.current.loading).toBe(false))

        expect(fetchEbookBlobMock).toHaveBeenCalledTimes(1)
        expect(first).not.toHaveBeenCalled()
        expect(second).toHaveBeenCalledTimes(1)
    })
})

describe('useEpubRendition — failure', () => {
    it('surfaces a blob fetch failure as error and clears loading', async () => {
        fetchEbookBlobMock.mockRejectedValue(new Error('403 nope'))
        const { result, onOpened } = renderRendition(7)

        await waitFor(() => expect(result.current.error).toBe('403 nope'))
        expect(result.current.loading).toBe(false)
        expect(ePubMock).not.toHaveBeenCalled()
        expect(onOpened).not.toHaveBeenCalled()
    })

    it('falls back to a generic message when the error has none, and catches onOpened throwing', async () => {
        const { book } = makeFakeBook()
        ePubMock.mockReturnValue(book)
        const onOpened = vi.fn().mockRejectedValue({})
        const { result } = renderRendition(7, { onOpened })

        await waitFor(() => expect(result.current.error).toBe('Failed to load ebook'))
        expect(result.current.loading).toBe(false)
        expect(book.locations.generate).not.toHaveBeenCalled()
    })
})

describe('useEpubRendition — teardown', () => {
    it('on unmount, runs onTeardown BEFORE destroying the book (the flush needs the iframe DOM)', async () => {
        const { book } = makeFakeBook()
        ePubMock.mockReturnValue(book)
        const order = []
        const onTeardown = vi.fn(() => order.push('teardown'))
        book.destroy.mockImplementation(() => order.push('destroy'))
        const { result, unmount, onOpened } = renderRendition(7, { onTeardown })
        await waitFor(() => expect(result.current.loading).toBe(false))
        const { isDestroyed } = onOpened.mock.calls[0][0]

        unmount()

        expect(order).toEqual(['teardown', 'destroy'])
        expect(isDestroyed()).toBe(true)
    })

    it('swallows a destroy() that throws', async () => {
        const { book } = makeFakeBook()
        ePubMock.mockReturnValue(book)
        book.destroy.mockImplementation(() => { throw new Error('already gone') })
        const { result, unmount } = renderRendition(7)
        await waitFor(() => expect(result.current.loading).toBe(false))

        expect(() => unmount()).not.toThrow()
    })

    it('never builds the book when unmounted during the blob fetch', async () => {
        let deliver
        fetchEbookBlobMock.mockReturnValue(new Promise(r => { deliver = r }))
        const { unmount, onTeardown } = renderRendition(7)

        unmount()
        await act(async () => { deliver(BUFFER) })

        expect(onTeardown).toHaveBeenCalledTimes(1)
        expect(ePubMock).not.toHaveBeenCalled()
    })

    it('after an unmount during the handoff: no spinner update, no locations pass, no error', async () => {
        const { book } = makeFakeBook()
        ePubMock.mockReturnValue(book)
        let finish
        const onOpened = vi.fn(() => new Promise(r => { finish = r }))
        const { result, unmount } = renderRendition(7, { onOpened })
        await waitFor(() => expect(onOpened).toHaveBeenCalled())

        unmount()
        await act(async () => { finish() })

        expect(book.destroy).toHaveBeenCalledTimes(1)
        expect(book.locations.generate).not.toHaveBeenCalled()
        expect(result.current.loading).toBe(true)
        expect(result.current.error).toBeNull()
    })

    it('does not report an error for a failure that arrives after unmount', async () => {
        let fail
        fetchEbookBlobMock.mockReturnValue(new Promise((_, reject) => { fail = reject }))
        const { result, unmount } = renderRendition(7)

        unmount()
        await act(async () => { fail(new Error('late')) })

        expect(result.current.error).toBeNull()
    })

    it('a new ebookId tears the old book down and opens the new one', async () => {
        const first = makeFakeBook()
        const second = makeFakeBook({ toc: [{ href: 'x.xhtml', label: 'X' }] })
        ePubMock.mockReturnValueOnce(first.book).mockReturnValueOnce(second.book)
        const { result, rerender, onTeardown } = renderRendition(7)
        await waitFor(() => expect(result.current.loading).toBe(false))

        rerender({ id: 8 })

        expect(first.book.destroy).toHaveBeenCalledTimes(1)
        expect(onTeardown).toHaveBeenCalledTimes(1)
        await waitFor(() => expect(result.current.bookRef.current).toBe(second.book))
        await waitFor(() => expect(result.current.toc).toEqual([{ href: 'x.xhtml', label: 'X' }]))
        expect(fetchEbookBlobMock).toHaveBeenLastCalledWith(8)
    })
})
