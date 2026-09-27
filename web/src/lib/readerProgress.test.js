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
    it('keeps at most 50 samples', () => {
        let s = []
        for (let i = 0; i < 60; i++) s = P.addSpeedSample(s, 1500, 60)
        expect(s).toHaveLength(50)
    })
})
