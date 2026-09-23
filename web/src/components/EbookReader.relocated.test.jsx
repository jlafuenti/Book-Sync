import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, waitFor, act } from '@testing-library/react'
import EbookReader from './EbookReader'

const {
    fetchEbookBlobMock, getPositionMock, updatePositionMock, matchTextToAudioMock,
    getDeviceIdMock, getDeviceNameMock, ePubMock, sendPositionKeepaliveMock, audioToEpubMock,
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
}))
vi.mock('epubjs', () => ({ default: ePubMock }))

// The lean epub.js double from EbookReader.test.jsx, with a multi-document
// spine so a relocation can land somewhere other than index 0.
function makeFakeBook(hrefs) {
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
    const book = {
        renderTo: vi.fn(() => rendition),
        loaded: { navigation: Promise.resolve({ toc: [] }) },
        spine: { items: hrefs.map(href => ({ href })), get: vi.fn(() => null) },
        locations: { generate: vi.fn().mockResolvedValue(undefined) },
        ready: Promise.resolve(),
        destroy: vi.fn(),
    }
    return { book, rendition, handlers }
}

beforeEach(() => {
    localStorage.clear()
    fetchEbookBlobMock.mockReset().mockResolvedValue(new ArrayBuffer(0))
    getPositionMock.mockReset().mockResolvedValue(null)   // unread: the gate is open
    updatePositionMock.mockReset().mockResolvedValue({})
    matchTextToAudioMock.mockReset().mockResolvedValue(null)
    getDeviceIdMock.mockReset().mockReturnValue('device-abc')
    getDeviceNameMock.mockReset().mockReturnValue('Web · Chrome')
    ePubMock.mockReset()
    sendPositionKeepaliveMock.mockReset()
    audioToEpubMock.mockReset().mockResolvedValue(null)
})

describe('EbookReader — the debounced save writes the chapter the relocation landed in (issue #278)', () => {
    // `saveProgress` used to be declared `(cfi, percent)` while the relocated
    // handler called it with a third `spineIndex` argument that was silently
    // dropped; doSave then fell back to `currentSpineIndexRef`, assigned two
    // lines earlier. Harmless by coincidence — this pins which chapter the
    // automatic write carries, so the plumbed argument cannot drift from it.

    it('after a relocated event at spine index N, the auto-save writes epub_chapter: N', async () => {
        const { book, rendition, handlers } = makeFakeBook(['cover.xhtml', 'ch1.xhtml', 'ch2.xhtml'])
        ePubMock.mockReturnValue(book)
        render(
            <EbookReader
                ebookId={7} pairId={42}
                initialChapter={null} initialTextPreview={null}
                bookTitle="Test Book" onClose={vi.fn()}
            />
        )
        await waitFor(() => expect(rendition.display).toHaveBeenCalled())

        act(() => {
            handlers.relocated({
                start: { cfi: 'cfi-ch2', percentage: 0.6, displayed: { page: 1, total: 1 }, href: 'ch2.xhtml' },
            })
        })
        await act(async () => { await new Promise(r => setTimeout(r, 2300)) })

        await waitFor(() => expect(updatePositionMock).toHaveBeenCalledTimes(1))
        expect(updatePositionMock).toHaveBeenCalledWith('pair', 42, expect.objectContaining({
            epub_chapter: 2,
            hint: { kind: 'epubjs_cfi', value: 'cfi-ch2' },
        }))
        expect(matchTextToAudioMock).toHaveBeenCalledWith(42, expect.any(String), 2)
    })

    it('a relocation whose href is not in the spine keeps the last known chapter', async () => {
        const { book, rendition, handlers } = makeFakeBook(['cover.xhtml', 'ch1.xhtml'])
        ePubMock.mockReturnValue(book)
        render(
            <EbookReader
                ebookId={7} pairId={42}
                initialChapter={null} initialTextPreview={null}
                bookTitle="Test Book" onClose={vi.fn()}
            />
        )
        await waitFor(() => expect(rendition.display).toHaveBeenCalled())

        act(() => {
            handlers.relocated({
                start: { cfi: 'cfi-a', percentage: 0.3, displayed: { page: 1, total: 1 }, href: 'ch1.xhtml' },
            })
        })
        act(() => {
            handlers.relocated({
                start: { cfi: 'cfi-b', percentage: 0.31, displayed: { page: 1, total: 1 }, href: 'nowhere.xhtml' },
            })
        })
        await act(async () => { await new Promise(r => setTimeout(r, 2300)) })

        await waitFor(() => expect(updatePositionMock).toHaveBeenCalledTimes(1))
        expect(updatePositionMock.mock.calls[0][2]).toMatchObject({ epub_chapter: 1, hint: { value: 'cfi-b' } })
    })
})

describe('EbookReader — a tab left open re-anchors after listening elsewhere (issue #683)', () => {
    // The reader opened on a page captured while listening was at 10:00.
    const OPENED = {
        anchor_revision: 0, source: 'audiobook', audio_position_ms: 600000, epub_chapter: 1,
        hints: [{
            kind: 'epubjs_cfi', value: 'cfi-old', anchor_revision: 0,
            device_id: 'device-abc', audio_position_ms: 600000,
        }],
    }
    // Meanwhile another device listened on to 30:00.
    const LISTENED_ON = { ...OPENED, audio_position_ms: 1800000 }

    let visibility = 'visible'
    beforeEach(() => {
        visibility = 'visible'
        Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => visibility })
        return () => { delete document.visibilityState }
    })
    const setVisibility = (state) => {
        visibility = state
        act(() => { document.dispatchEvent(new Event('visibilitychange')) })
    }

    async function openReader() {
        const fake = makeFakeBook(['cover.xhtml', 'ch1.xhtml', 'ch2.xhtml'])
        ePubMock.mockReturnValue(fake.book)
        getPositionMock.mockResolvedValueOnce(OPENED)
        render(
            <EbookReader
                ebookId={7} pairId={42}
                initialChapter={null} initialTextPreview={null}
                bookTitle="Test Book" onClose={vi.fn()}
            />
        )
        await waitFor(() => expect(fake.rendition.display).toHaveBeenCalledWith('cfi-old'))
        await waitFor(() => expect(fake.handlers.relocated).toBeDefined())
        return fake
    }

    it('coming back to the tab re-runs the ladder and lands where listening stopped', async () => {
        const { rendition } = await openReader()
        getPositionMock.mockResolvedValue(LISTENED_ON)
        audioToEpubMock.mockResolvedValue({ epub_chapter: 2, preview: null })

        setVisibility('hidden')
        setVisibility('visible')

        await waitFor(() => expect(rendition.display).toHaveBeenCalledWith('ch2.xhtml'))
        expect(getPositionMock).toHaveBeenCalledTimes(2)
        expect(audioToEpubMock).toHaveBeenCalledWith(42, 1800000)
    })

    it('holds every save while the fresh record is in flight — a page turn from the stale page is dropped', async () => {
        const { rendition, handlers } = await openReader()
        let release
        getPositionMock.mockReturnValue(new Promise(r => { release = r }))

        setVisibility('hidden')
        setVisibility('visible')

        // The user turns a page off the stale page before the fetch returns.
        act(() => { window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight' })) })
        act(() => {
            handlers.relocated({
                start: { cfi: 'cfi-old-plus-one', percentage: 0.2, displayed: { page: 2, total: 9 }, href: 'ch1.xhtml' },
            })
        })
        await act(async () => { await new Promise(r => setTimeout(r, 2300)) })
        expect(updatePositionMock).not.toHaveBeenCalled()

        audioToEpubMock.mockResolvedValue({ epub_chapter: 2, preview: null })
        await act(async () => { release(LISTENED_ON) })
        await waitFor(() => expect(rendition.display).toHaveBeenCalledWith('ch2.xhtml'))
        expect(updatePositionMock).not.toHaveBeenCalled()
    })

    it('does nothing when listening has not moved on', async () => {
        const { rendition } = await openReader()
        getPositionMock.mockResolvedValue({ ...OPENED, audio_position_ms: 610000 })

        setVisibility('hidden')
        setVisibility('visible')

        await waitFor(() => expect(getPositionMock).toHaveBeenCalledTimes(2))
        await act(async () => { await new Promise(r => setTimeout(r, 50)) })
        expect(rendition.display).toHaveBeenCalledTimes(1)
        expect(audioToEpubMock).not.toHaveBeenCalled()
    })

    it('does nothing when the record was last written by a reader', async () => {
        const { rendition } = await openReader()
        getPositionMock.mockResolvedValue({ ...LISTENED_ON, source: 'ebook' })

        setVisibility('hidden')
        setVisibility('visible')

        await waitFor(() => expect(getPositionMock).toHaveBeenCalledTimes(2))
        await act(async () => { await new Promise(r => setTimeout(r, 50)) })
        expect(rendition.display).toHaveBeenCalledTimes(1)
    })

    it("counts the reader's own saves: an audio position it wrote is not 'listening moved on'", async () => {
        const { rendition, handlers } = await openReader()
        // The settle save's sync-map match moves the record's audio to 30:00.
        matchTextToAudioMock.mockResolvedValue({
            epub_chapter: 2, epub_sentence_index: 40, sync_map_version: 1, audio_position_ms: 1800000,
        })
        act(() => {
            handlers.relocated({
                start: { cfi: 'cfi-read-on', percentage: 0.6, displayed: { page: 1, total: 1 }, href: 'ch2.xhtml' },
            })
        })
        await act(async () => { await new Promise(r => setTimeout(r, 2300)) })
        await waitFor(() => expect(updatePositionMock).toHaveBeenCalledTimes(1))

        getPositionMock.mockResolvedValue(LISTENED_ON)
        setVisibility('hidden')
        setVisibility('visible')

        await waitFor(() => expect(getPositionMock).toHaveBeenCalledTimes(2))
        await act(async () => { await new Promise(r => setTimeout(r, 50)) })
        expect(rendition.display).toHaveBeenCalledTimes(1)
    })

    it('a tab that was never hidden does not re-fetch on a visible event', async () => {
        await openReader()
        setVisibility('visible')
        await act(async () => { await new Promise(r => setTimeout(r, 50)) })
        expect(getPositionMock).toHaveBeenCalledTimes(1)
    })
})
