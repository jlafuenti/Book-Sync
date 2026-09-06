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
