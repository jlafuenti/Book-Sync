import { readFileSync } from 'fs'
import { resolve } from 'path'
import { describe, it, expect } from 'vitest'
import * as P from './readerProgress'

const cases = JSON.parse(readFileSync(resolve(__dirname, '../../../server/tests/fixtures/sync_parity/reader_progress_cases.json'), 'utf8'))

describe('reader progress rules (issue #730, shared with Android)', () => {
    it('cycles modes', () => { for (const [from, to] of cases.next_mode) expect(P.nextProgressMode(from)).toBe(to) })
    it('parses progress mode', () => { for (const [v, want] of cases.parse_progress_mode) expect(P.parseProgressMode(v)).toBe(want) })
    it('parses page mode', () => { for (const [v, want] of cases.parse_page_mode) expect(P.parsePageMode(v)).toBe(want) })
    it('page in section', () => { for (const c of cases.page_in_section) expect(P.pageInSection(c.progression, c.total)).toBe(c.expect) })
    it('ebook position', () => { for (const c of cases.ebook_position) expect(P.ebookPosition(c.counts, c.section, c.page)).toEqual(c.expect) })
    it('resolves page labels', () => {
        for (const c of cases.resolve_page_label) {
            const r = P.resolvePageLabel(c.in)
            expect(r, c.name).toEqual(c.expect)
            expect(P.formatPageLabel(r), c.name).toBe(c.text)
        }
    })
    it('chapter text', () => { for (const [p, t, want] of cases.chapter_text) expect(P.formatChapterPage(p, t)).toBe(want) })
    it('reading speed', () => { for (const c of cases.speed) expect(P.charsPerSecond(c.samples), c.name).toBeCloseTo(c.expect, 9) })
    it('adds samples', () => { for (const c of cases.add_sample) expect(P.addSpeedSample(c.samples, c.chars, c.dwell)).toEqual(c.expect) })
    it('seconds left', () => {
        for (const c of cases.seconds_left) {
            const got = P.secondsLeftInSection(c.chars, c.page, c.pages, c.cps)
            if (c.expect === null) expect(got).toBeNull(); else expect(got).toBeCloseTo(c.expect, 6)
        }
    })
    it('time text', () => { for (const [s, want] of cases.time_text) expect(P.formatTimeLeft(s)).toBe(want) })
    it('fallback notice', () => {
        expect(P.FALLBACK_NOTICE).toBe(cases.fallback_notice.text)
        expect(P.FALLBACK_NOTICE_MS).toBe(cases.fallback_notice.ms)
    })
    it('keeps at most 50 samples', () => {
        let s = []
        for (let i = 0; i < 60; i++) s = P.addSpeedSample(s, 1500, 60)
        expect(s).toHaveLength(50)
    })

    // JSON cannot carry NaN, so these are plain assertions rather than fixture cases —
    // pinned on both platforms (ReaderProgressParityTest.kt has the Kotlin equivalents).
    it('rejects a NaN sample or dwell time', () => {
        expect(P.addSpeedSample([], NaN, 60)).toEqual([])
        expect(P.addSpeedSample([], 1500, NaN)).toEqual([])
    })
    it('rejects a NaN reading speed', () => {
        expect(P.secondsLeftInSection(1000, 1, 2, NaN)).toBeNull()
    })
    it('treats a NaN progression as the start of the section', () => {
        expect(P.pageInSection(NaN, 10)).toBe(1)
    })

    it('treats a non-array samples list (e.g. read back from localStorage) as empty', () => {
        expect(P.addSpeedSample(null, 1500, 60)).toEqual([25])
    })

    it('exercises every section of the fixture', () => {
        const covered = new Set([
            'next_mode', 'parse_progress_mode', 'parse_page_mode', 'page_in_section', 'ebook_position',
            'resolve_page_label', 'chapter_text', 'speed', 'add_sample', 'seconds_left', 'time_text',
            'fallback_notice',
        ])
        const keys = new Set(Object.keys(cases).filter(k => k !== '_doc'))
        expect(keys).toEqual(covered)
    })
})
