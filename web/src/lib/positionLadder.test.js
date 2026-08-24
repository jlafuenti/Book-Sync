import { describe, it, expect } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import {
    planRestore, hasAnchor, isStartOfBook, navigationEstablishesPosition,
} from './positionLadder'

/**
 * Cross-platform parity: this suite and the Python one
 * (`server/tests/test_position_resolver.py`) load the *same* golden vectors,
 * so the web ladder and the server ladder cannot drift apart silently.
 */
const FIXTURE = path.resolve(
    __dirname, '../../../server/tests/fixtures/sync_parity/restore_cases.json'
)
const CASES = JSON.parse(fs.readFileSync(FIXTURE, 'utf-8'))

describe('planRestore parity vectors', () => {
    it.each(CASES.map(c => [c.name, c]))('%s', (_name, testCase) => {
        const steps = planRestore(testCase.position, {
            spineCount: testCase.context.spine_count,
            deviceId: testCase.context.device_id,
            hintKind: testCase.context.hint_kind,
        })
        expect(steps.map(s => s.kind), testCase.why).toEqual(testCase.expected)
    })
})

describe('the never-empty invariant', () => {
    // Stated separately from the fixtures because the whole design rests on
    // it: a position that holds an anchor must never resolve to "start of
    // book", which is what let a real position be overwritten with chapter 0.
    const anchored = [
        { anchor_revision: 1, epub_chapter: 3 },
        { anchor_revision: 1, epub_text_preview: 'althea counted the ships at anchor' },
        { anchor_revision: 1, epub_progress_percent: 12.5 },
        { anchor_revision: 1, audio_position_ms: 1000 },
    ]

    it.each(anchored)('yields a plan for %j', (position) => {
        const steps = planRestore(position, {
            spineCount: 40, deviceId: 'web', hintKind: 'epubjs_cfi',
        })
        expect(steps.length).toBeGreaterThan(0)
        expect(hasAnchor(position)).toBe(true)
    })

    it('reports no anchor only for a genuinely empty record', () => {
        expect(hasAnchor(null)).toBe(false)
        expect(hasAnchor({ anchor_revision: 1 })).toBe(false)
        expect(hasAnchor({ anchor_revision: 1, epub_progress_percent: 0 })).toBe(false)
    })
})

describe('step payloads', () => {
    it('carries what the reader needs to execute each step', () => {
        const steps = planRestore({
            anchor_revision: 7,
            epub_chapter: 12,
            epub_text_preview: 'althea counted the ships at anchor',
            epub_progress_percent: 46.81,
            hints: [{
                kind: 'epubjs_cfi', device_id: 'web-a',
                value: 'epubcfi(/6/26!/4/2)', anchor_revision: 7,
            }],
        }, { spineCount: 40, deviceId: 'web-b', hintKind: 'epubjs_cfi' })

        const byKind = Object.fromEntries(steps.map(s => [s.kind, s]))
        // CFIs are portable between browsers, so web-b may use web-a's.
        expect(byKind.hint.value).toBe('epubcfi(/6/26!/4/2)')
        expect(byKind.text.text).toBe('althea counted the ships at anchor')
        expect(byKind.text.seedChapter).toBe(12)
        expect(byKind.chapter.chapter).toBe(12)
        expect(byKind.percent.percent).toBe(46.81)
    })
})

describe('the navigation clause (issue #159)', () => {
    // Web mirror of Android's PositionSavePolicy: a deliberate user
    // navigation makes the current position the truth and reopens the write
    // gate after an unresolved restore — EXCEPT while still sitting at the
    // start of the book (the start-of-book backstop): no navigation signal is
    // trustworthy enough to let a start-of-book write replace a real anchor,
    // and a genuine forward page-turn moves off the start anyway.

    it('a user navigation off the start establishes the position', () => {
        expect(navigationEstablishesPosition({
            userNavigated: true, spineIndex: 0, chapterProgression: 0.25,
        })).toBe(true)
        expect(navigationEstablishesPosition({
            userNavigated: true, spineIndex: 3, chapterProgression: 0,
        })).toBe(true)
    })

    it('never establishes while still at the start of the book', () => {
        expect(navigationEstablishesPosition({
            userNavigated: true, spineIndex: 0, chapterProgression: 0,
        })).toBe(false)
    })

    it('never establishes without a user navigation, wherever the view sits', () => {
        // The restore/text-nav pass relocates the view too; those relocations
        // are the reader's own guesses, not the user's choice.
        expect(navigationEstablishesPosition({
            userNavigated: false, spineIndex: 7, chapterProgression: 0.5,
        })).toBe(false)
    })

    it('isStartOfBook treats missing values as the start (fail safe)', () => {
        expect(isStartOfBook({ spineIndex: null, chapterProgression: null })).toBe(true)
        expect(isStartOfBook({ spineIndex: 0, chapterProgression: 0 })).toBe(true)
        expect(isStartOfBook({ spineIndex: 0, chapterProgression: 0.1 })).toBe(false)
        expect(isStartOfBook({ spineIndex: 1, chapterProgression: 0 })).toBe(false)
    })
})
