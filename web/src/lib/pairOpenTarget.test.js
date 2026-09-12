import { describe, it, expect } from 'vitest'
import fs from 'fs'
import path from 'path'
import { resolvePairOpenTarget } from './pairOpenTarget'

/**
 * Cross-platform parity (issue #215): this suite and Android's
 * `ResolvePairOpenTargetTest` load the *same* golden vectors, so the two
 * clients cannot disagree about which format a pair opens in.
 *
 * The web keyed on equal `updated_at` timestamps instead of `bookmarks.source`
 * until this landed — and because a pair-scoped write stamps both
 * `user_progress` rows in the same loop, that comparison always tied and
 * always resolved to the reader.
 */
const FIXTURE = path.resolve(
    __dirname, '../../../server/tests/fixtures/sync_parity/pair_open_target.json'
)
const CASES = JSON.parse(fs.readFileSync(FIXTURE, 'utf-8'))

describe('resolvePairOpenTarget parity vectors', () => {
    it.each(CASES.map(c => [c.name, c]))('%s', (_name, testCase) => {
        // `local` defaults to `available`, which is what the web always passes:
        // a format present on the pair is present, full stop. Android is the
        // client where the two diverge (issue #484) — a book it can stream is
        // openable without being on the device.
        const local = testCase.local ?? testCase.available
        const target = resolvePairOpenTarget(testCase.source, {
            hasEbook: testCase.available.ebook,
            hasAudiobook: testCase.available.audiobook,
            localEbook: local.ebook,
            localAudiobook: local.audiobook,
        })
        expect(target, testCase.why).toBe(testCase.expected)
    })

    it('exercises the openable-but-not-local split the second axis exists for', () => {
        // Without at least one such vector the `local` parameter is untested and
        // the web silently keeps its old single-axis behaviour.
        expect(CASES.some(c => c.local && (
            c.local.ebook !== c.available.ebook || c.local.audiobook !== c.available.audiobook
        ))).toBe(true)
    })

    it('covers both sources, the absent source, and the details fallback', () => {
        const expected = new Set(CASES.map(c => c.expected))
        expect(expected).toEqual(new Set(['ebook', 'audiobook', 'details']))
        expect(CASES.some(c => c.source === null)).toBe(true)
    })
})

describe('resolvePairOpenTarget defaults', () => {
    it('treats a missing availability object as nothing being openable', () => {
        expect(resolvePairOpenTarget('audiobook')).toBe('details')
    })

    it('ignores a source that is neither format', () => {
        // Defensive: an unknown wire value must not route anywhere odd.
        expect(resolvePairOpenTarget('podcast', { hasEbook: true, hasAudiobook: true }))
            .toBe('ebook')
    })

    it('defaults local to openable, so existing callers are unchanged', () => {
        // The web passes neither `localEbook` nor `localAudiobook`; every
        // call site in web/src relies on this default.
        expect(resolvePairOpenTarget(null, { hasEbook: true, hasAudiobook: true }))
            .toBe('ebook')
        expect(resolvePairOpenTarget(null, { hasEbook: false, hasAudiobook: true }))
            .toBe('audiobook')
    })
})
