import { describe, it, expect, vi, beforeEach } from 'vitest'
import {
    executeRestore, fallbackPosition, fetchOpeningPosition, classifyLanding,
    restorePosition, createWriteGate,
} from './restoreController'

const { getPositionMock, audioToEpubMock, getDeviceIdMock } = vi.hoisted(() => ({
    getPositionMock: vi.fn(),
    audioToEpubMock: vi.fn(),
    getDeviceIdMock: vi.fn(() => 'device-abc'),
}))

vi.mock('../api', () => ({
    getPosition: getPositionMock,
    audioToEpub: audioToEpubMock,
    getDeviceId: getDeviceIdMock,
}))

beforeEach(() => {
    getPositionMock.mockReset().mockResolvedValue(null)
    audioToEpubMock.mockReset().mockResolvedValue(null)
    getDeviceIdMock.mockReset().mockReturnValue('device-abc')
})

// The same minimal epub.js double EbookReader.test.jsx drives the reader with.
function makeBook(hrefs = ['ch0.xhtml', 'ch1.xhtml', 'ch2.xhtml']) {
    const rendition = { display: vi.fn().mockResolvedValue(undefined) }
    const book = { spine: { items: hrefs.map(href => ({ href })) } }
    return { book, rendition }
}

describe('fetchOpeningPosition — the canonical record, or the props when offline', () => {
    it('reads the pair record for a paired book', async () => {
        getPositionMock.mockResolvedValue({ anchor_revision: 3, epub_chapter: 4, hints: [] })
        const position = await fetchOpeningPosition({ pairId: 42, ebookId: 7 })
        expect(getPositionMock).toHaveBeenCalledWith('pair', 42)
        expect(position).toEqual({ anchor_revision: 3, epub_chapter: 4, hints: [] })
    })

    it('reads the standalone ebook record when there is no pair', async () => {
        await fetchOpeningPosition({ pairId: null, ebookId: 7 })
        expect(getPositionMock).toHaveBeenCalledWith('ebook', 7)
    })

    it('falls back to the portable anchor from the props when the fetch fails', async () => {
        getPositionMock.mockRejectedValue(new Error('offline'))
        const position = await fetchOpeningPosition({
            pairId: 42, ebookId: 7, initialChapter: 3, initialTextPreview: 'the text',
        })
        expect(position).toEqual({
            anchor_revision: 0, epub_chapter: 3, epub_text_preview: 'the text', hints: [],
        })
    })

    it('chapter 0 from the props is still an anchor; an empty preview is omitted', () => {
        expect(fallbackPosition({ initialChapter: 0, initialTextPreview: null })).toEqual({
            anchor_revision: 0, epub_chapter: 0, epub_text_preview: undefined, hints: [],
        })
    })

    it('is null (open at the start) when the fetch fails and there is no chapter prop', async () => {
        getPositionMock.mockRejectedValue(new Error('offline'))
        expect(await fetchOpeningPosition({ pairId: 42, ebookId: 7, initialChapter: null })).toBeNull()
        expect(fallbackPosition({})).toBeNull()
    })
})

describe('executeRestore — walks the ladder, landing on the first step that works', () => {
    // EbookReader.test.jsx keeps the original suite for this function via the
    // reader's re-export; these pin the rungs it does not cover.

    it('lands on a hint step by displaying the CFI', async () => {
        const { book, rendition } = makeBook()
        expect(await executeRestore(book, rendition, [{ kind: 'hint', value: 'cfi-1' }])).toBe(true)
        expect(rendition.display).toHaveBeenCalledWith('cfi-1')
    })

    it('text and audio rungs cannot land the initial display; they fall through', async () => {
        const { book, rendition } = makeBook()
        const landed = await executeRestore(book, rendition, [
            { kind: 'text', text: 'a preview', seedChapter: 1 },
            { kind: 'audio', audioPositionMs: 5000 },
            { kind: 'chapter', chapter: 1 },
        ])
        expect(landed).toBe(true)
        expect(rendition.display).toHaveBeenCalledTimes(1)
        expect(rendition.display).toHaveBeenCalledWith('ch1.xhtml')
    })

    it('a percent step with no locations yet is skipped, not a landing', async () => {
        const { book, rendition } = makeBook()
        expect(await executeRestore(book, rendition, [{ kind: 'percent', percent: 40 }])).toBe(false)
        expect(rendition.display).toHaveBeenCalledWith()
    })

    it('a step that throws is swallowed and the ladder continues', async () => {
        const { book, rendition } = makeBook()
        rendition.display.mockRejectedValueOnce(new Error('boom'))
        const landed = await executeRestore(book, rendition, [
            { kind: 'chapter', chapter: 0 }, { kind: 'chapter', chapter: 2 },
        ])
        expect(landed).toBe(true)
        expect(rendition.display).toHaveBeenLastCalledWith('ch2.xhtml')
    })
})

describe('classifyLanding — the verdict the write gate is set from', () => {
    const anchored = { anchor_revision: 1, epub_chapter: 900, hints: [] }
    const step = [{ kind: 'chapter', chapter: 1 }]

    it('a rung from the record landed: established, saving allowed', () => {
        expect(classifyLanding({ landed: true, steps: step, position: anchored, audioDerived: false }))
            .toEqual({ kind: 'landed', positionEstablished: true, restoreLanded: true, unresolved: false })
    })

    it('every rung failed: unresolved, gate closed', () => {
        expect(classifyLanding({ landed: false, steps: step, position: anchored, audioDerived: false }))
            .toEqual({ kind: 'unresolved', positionEstablished: false, restoreLanded: false, unresolved: true })
    })

    it('an anchored record whose plan came out empty is unresolved, not unread (issue #159)', () => {
        // A re-parsed EPUB left epub_chapter past this spine: planning drops
        // the un-navigable rung, the empty plan opened at page one and
        // "landed" — treating that as unread is the silent gate-open.
        expect(classifyLanding({ landed: true, steps: [], position: anchored, audioDerived: false }).kind)
            .toBe('unresolved')
    })

    it('a genuinely empty record with an empty plan is a landing at the start of the book', () => {
        expect(classifyLanding({ landed: true, steps: [], position: null, audioDerived: false }).kind)
            .toBe('landed')
    })

    it('an audio-derived chapter with a searchable preview is only PROVISIONAL', () => {
        // The chapter came from the sync map, not the record; displaying it
        // is a guess until the text-nav pass finds the preview.
        const position = { ...anchored, epub_chapter: 1, epub_text_preview: 'gwendolyn felt herself' }
        expect(classifyLanding({ landed: true, steps: step, position, audioDerived: true }))
            .toEqual({ kind: 'provisional', positionEstablished: false, restoreLanded: false, unresolved: false })
    })

    it('an audio-derived chapter with nothing to confirm it stays unresolved', () => {
        const position = { ...anchored, epub_chapter: 1, epub_text_preview: '   ' }
        expect(classifyLanding({ landed: true, steps: step, position, audioDerived: true }))
            .toEqual({ kind: 'unconfirmable', positionEstablished: false, restoreLanded: false, unresolved: true })
    })

    it('an audio-derived restore that did not land is plainly unresolved', () => {
        const position = { ...anchored, epub_chapter: 1, epub_text_preview: 'some preview text' }
        expect(classifyLanding({ landed: false, steps: step, position, audioDerived: true }).kind)
            .toBe('unresolved')
    })
})

describe('restorePosition — fetch, plan, resolve the audio rung, execute, classify', () => {
    const args = (extra = {}) => ({
        pairId: 42, ebookId: 7, initialChapter: null, initialTextPreview: null, ...extra,
    })

    it('lands on a current epub.js CFI hint first and reports the record it opened with', async () => {
        const record = {
            anchor_revision: 2, epub_chapter: 1,
            hints: [{ kind: 'epubjs_cfi', value: 'cfi-hint', anchor_revision: 2, device_id: 'other' }],
        }
        getPositionMock.mockResolvedValue(record)
        const { book, rendition } = makeBook()

        const result = await restorePosition({ book, rendition, ...args() })

        expect(rendition.display).toHaveBeenCalledTimes(1)
        expect(rendition.display).toHaveBeenCalledWith('cfi-hint')
        expect(result.position).toBe(record)
        expect(result.outcome.kind).toBe('landed')
        expect(getDeviceIdMock).toHaveBeenCalled()
    })

    it('a chapter past this spine with nothing else is unresolved', async () => {
        getPositionMock.mockResolvedValue({ anchor_revision: 5, epub_chapter: 900, hints: [] })
        const { book, rendition } = makeBook()

        const result = await restorePosition({ book, rendition, ...args() })

        expect(rendition.display).toHaveBeenCalledWith()
        expect(result.outcome.kind).toBe('unresolved')
    })

    it('an unread book opens at the start and is established', async () => {
        const { book, rendition } = makeBook()
        const result = await restorePosition({ book, rendition, ...args() })
        expect(rendition.display).toHaveBeenCalledWith()
        expect(result.position).toBeNull()
        expect(result.outcome.kind).toBe('landed')
    })

    it('resolves a listen-only record through the server and re-plans from the derived chapter', async () => {
        getPositionMock.mockResolvedValue({
            anchor_revision: 0, source: 'audiobook', audio_position_ms: 600000, hints: [],
        })
        audioToEpubMock.mockResolvedValue({ epub_chapter: 1, preview: 'Gwendolyn felt herself smile.' })
        const { book, rendition } = makeBook()

        const result = await restorePosition({ book, rendition, ...args() })

        expect(audioToEpubMock).toHaveBeenCalledWith(42, 600000)
        expect(rendition.display).toHaveBeenCalledWith('ch1.xhtml')
        expect(result.position).toMatchObject({
            audio_position_ms: 600000, epub_chapter: 1,
            epub_text_preview: 'Gwendolyn felt herself smile.',
        })
        // A guess until the text-nav pass confirms it.
        expect(result.outcome.kind).toBe('provisional')
    })

    it('keeps the record preview when the server resolves a chapter without one', async () => {
        getPositionMock.mockResolvedValue({
            anchor_revision: 0, audio_position_ms: 600000, hints: [],
        })
        audioToEpubMock.mockResolvedValue({ epub_chapter: 2, preview: '' })
        const { book, rendition } = makeBook()

        const result = await restorePosition({ book, rendition, ...args() })

        expect(result.position.epub_text_preview).toBeUndefined()
        expect(result.outcome.kind).toBe('unconfirmable')
    })

    it('an audio rung the server cannot resolve leaves the plan alone: unresolved', async () => {
        getPositionMock.mockResolvedValue({
            anchor_revision: 0, audio_position_ms: 600000, hints: [],
        })
        audioToEpubMock.mockRejectedValue(new Error('no sync map'))
        const { book, rendition } = makeBook()

        const result = await restorePosition({ book, rendition, ...args() })

        expect(rendition.display).toHaveBeenCalledWith()
        expect(result.position.epub_chapter).toBeUndefined()
        expect(result.outcome.kind).toBe('unresolved')
    })

    it('never asks the server about audio for an unpaired ebook', async () => {
        getPositionMock.mockResolvedValue({
            anchor_revision: 0, audio_position_ms: 600000, hints: [],
        })
        const { book, rendition } = makeBook()

        await restorePosition({ book, rendition, ...args({ pairId: null }) })

        expect(audioToEpubMock).not.toHaveBeenCalled()
    })

    it('the audio rung is only taken when it is the FIRST (i.e. only) rung', async () => {
        getPositionMock.mockResolvedValue({
            anchor_revision: 0, epub_chapter: 2, audio_position_ms: 600000, hints: [],
        })
        const { book, rendition } = makeBook()

        const result = await restorePosition({ book, rendition, ...args() })

        expect(audioToEpubMock).not.toHaveBeenCalled()
        expect(rendition.display).toHaveBeenCalledWith('ch2.xhtml')
        expect(result.outcome.kind).toBe('landed')
    })

    it('stops without displaying anything once the reader is torn down mid-fetch', async () => {
        let destroyed = false
        getPositionMock.mockImplementation(async () => { destroyed = true; return null })
        const { book, rendition } = makeBook()

        const result = await restorePosition({ book, rendition, ...args({ isDestroyed: () => destroyed }) })

        expect(result).toBeNull()
        expect(rendition.display).not.toHaveBeenCalled()
    })

    it('stops after the audio round-trip when torn down during it', async () => {
        let destroyed = false
        getPositionMock.mockResolvedValue({ anchor_revision: 0, audio_position_ms: 1000, hints: [] })
        audioToEpubMock.mockImplementation(async () => { destroyed = true; return { epub_chapter: 1, preview: 'x' } })
        const { book, rendition } = makeBook()

        const result = await restorePosition({ book, rendition, ...args({ isDestroyed: () => destroyed }) })

        expect(result).toBeNull()
        expect(rendition.display).not.toHaveBeenCalled()
    })

    it('uses the props as the record when offline', async () => {
        getPositionMock.mockRejectedValue(new Error('offline'))
        const { book, rendition } = makeBook()

        const result = await restorePosition({ book, rendition, ...args({ initialChapter: 2 }) })

        expect(rendition.display).toHaveBeenCalledWith('ch2.xhtml')
        expect(result.outcome.kind).toBe('landed')
    })
})

describe('createWriteGate — nothing is written until the restore has landed', () => {
    const offStart = { spineIndex: 0, chapterProgression: 0.4 }
    const atStart = { spineIndex: 0, chapterProgression: 0 }

    it('starts closed, with no landing and no navigation', () => {
        const gate = createWriteGate()
        expect(gate.positionEstablished).toBe(false)
        expect(gate.restoreLanded).toBe(false)
        expect(gate.audioConfirmPending).toBe(false)
        expect(gate.userNavigated).toBe(false)
        expect(gate.maybeOpen(offStart)).toBe('closed')
    })

    it('a landed restore opens it and marks the landing genuine', () => {
        const gate = createWriteGate()
        gate.applyLanding(classifyLanding({ landed: true, steps: [{ kind: 'hint' }], position: {}, audioDerived: false }))
        expect(gate.positionEstablished).toBe(true)
        expect(gate.restoreLanded).toBe(true)
        expect(gate.maybeOpen(atStart)).toBe('open')
    })

    it('an unresolved restore keeps it closed until a user page turn off the start', () => {
        const gate = createWriteGate()
        gate.applyLanding(classifyLanding({ landed: false, steps: [{ kind: 'chapter' }], position: {}, audioDerived: false }))
        expect(gate.maybeOpen(offStart)).toBe('closed')

        // Relocations from the restore itself are not navigation.
        gate.noteUserNavigation()
        expect(gate.maybeOpen(atStart)).toBe('closed')   // start-of-book backstop
        expect(gate.maybeOpen(offStart)).toBe('opened')  // the navigation clause
        expect(gate.positionEstablished).toBe(true)
        // The landing was never genuine: the matcher must stay out of writes.
        expect(gate.restoreLanded).toBe(false)
        expect(gate.maybeOpen(offStart)).toBe('open')
    })

    it('a provisional landing waits for the text-nav pass; confirmation opens it, once', () => {
        const gate = createWriteGate()
        const provisional = classifyLanding({
            landed: true, steps: [{ kind: 'chapter' }],
            position: { epub_text_preview: 'preview text' }, audioDerived: true,
        })
        gate.applyLanding(provisional)
        expect(gate.audioConfirmPending).toBe(true)
        expect(gate.positionEstablished).toBe(false)
        expect(gate.maybeOpen(offStart)).toBe('closed')

        expect(gate.confirmProvisionalLanding()).toBe(true)
        expect(gate.audioConfirmPending).toBe(false)
        expect(gate.positionEstablished).toBe(true)
        expect(gate.restoreLanded).toBe(true)
        expect(gate.confirmProvisionalLanding()).toBe(false)
    })

    it('a failed confirmation leaves the gate closed and reports the failure once', () => {
        const gate = createWriteGate()
        gate.applyLanding(classifyLanding({
            landed: true, steps: [{ kind: 'chapter' }],
            position: { epub_text_preview: 'preview text' }, audioDerived: true,
        }))

        expect(gate.failProvisionalLanding()).toBe(true)
        expect(gate.audioConfirmPending).toBe(false)
        expect(gate.positionEstablished).toBe(false)
        expect(gate.restoreLanded).toBe(false)
        expect(gate.failProvisionalLanding()).toBe(false)
        expect(gate.confirmProvisionalLanding()).toBe(false)
    })

    it('confirmation is a no-op when nothing was pending (an unconfirmable derived chapter)', () => {
        const gate = createWriteGate()
        gate.applyLanding(classifyLanding({ landed: true, steps: [{ kind: 'chapter' }], position: {}, audioDerived: true }))
        expect(gate.audioConfirmPending).toBe(false)
        expect(gate.confirmProvisionalLanding()).toBe(false)
        expect(gate.failProvisionalLanding()).toBe(false)
        expect(gate.positionEstablished).toBe(false)
    })
})
