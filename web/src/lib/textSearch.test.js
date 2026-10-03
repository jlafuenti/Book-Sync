import { describe, it, expect } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import { normalizeForSearch, extractSearchableText, findTextPosition } from './textSearch'

/**
 * Cross-platform parity (issue #305): the web reader's spine search must
 * normalize text exactly like `server/services/sync_matcher.py::
 * normalize_for_search` (mirrored in Kotlin as `SyncMatcher.normalizeForSearch`)
 * — the needles it searches for are server-derived sync-point previews, so any
 * divergence makes real text unfindable. This suite loads the SAME golden
 * vectors the Python and Kotlin suites are pinned to.
 */
const FIXTURE = path.resolve(
    __dirname, '../../../server/tests/fixtures/sync_parity/normalize_cases.json'
)
const CASES = JSON.parse(fs.readFileSync(FIXTURE, 'utf-8'))

describe('normalizeForSearch parity vectors', () => {
    it.each(CASES.map(c => [JSON.stringify(c.input), c]))('%s', (_name, c) => {
        expect(normalizeForSearch(c.input)).toBe(c.expected)
    })

    // The live failure class, stated explicitly: a single NBSP between words
    // must become a space, never vanish and glue the words together — that is
    // exactly how "Gwendolyn felt herself smile slightly." went unfound.
    it('converts unicode whitespace variants to spaces instead of deleting them', () => {
        expect(normalizeForSearch('Gwendolyn\u00a0felt herself\u00a0smile slightly.'))
            .toBe('gwendolyn felt herself smile slightly')
    })

    it('strips smart quotes and apostrophes the way the server does', () => {
        expect(normalizeForSearch('“Don’t stop,” she said.'))
            .toBe('dont stop she said')
    })

    it('handles null/undefined defensively', () => {
        expect(normalizeForSearch(null)).toBe('')
        expect(normalizeForSearch(undefined)).toBe('')
    })
})

describe('extractSearchableText', () => {
    // The server extracts sync-point previews by concatenating a block's text
    // nodes exactly as they are, so inline markup adds nothing (issue #799:
    // `<i>W</i>ord` is "Word", `<b>Name</b>: x` is "Name: x"). Block-level
    // boundaries and <br> still separate words. This extractor mirrors that;
    // plain `textContent` would glue words across blocks.

    function el(html) {
        const div = document.createElement('div')
        div.innerHTML = html
        return div
    }

    it('adds nothing at inline element boundaries', () => {
        const root = el('<p>And then <i>Gwendolyn</i>felt herself <em>smile</em>slightly. More.</p>')
        expect(normalizeForSearch(extractSearchableText(root)))
            .toContain('gwendolynfelt herself smileslightly')
    })

    it('does not split a word or put a space before punctuation at inline boundaries', () => {
        expect(normalizeForSearch(extractSearchableText(el('<p><i>W</i>ord by word</p>'))))
            .toBe('word by word')
        expect(extractSearchableText(el('<p><b>Name</b>: text</p>')).trim()).toBe('Name: text')
    })

    it('keeps whitespace-only text between inline elements as a space', () => {
        const root = el('<p><i>left</i> <i>right</i> side</p>')
        expect(normalizeForSearch(extractSearchableText(root))).toBe('left right side')
    })

    it('separates block elements, line breaks and table cells', () => {
        expect(normalizeForSearch(extractSearchableText(el('<p>one</p><p>two</p>')))).toBe('one two')
        expect(normalizeForSearch(extractSearchableText(el('<div>a<p>b</p>c</div>')))).toBe('a b c')
        expect(normalizeForSearch(extractSearchableText(el('<p>first<br>second</p>')))).toBe('first second')
        expect(normalizeForSearch(extractSearchableText(el('<table><tr><td>x</td><td>y</td></tr></table>'))))
            .toBe('x y')
    })

    it('keeps ordinary spacing intact', () => {
        const root = el('<p>Gwendolyn felt herself <i>smile</i> slightly.</p>')
        expect(normalizeForSearch(extractSearchableText(root)))
            .toBe('gwendolyn felt herself smile slightly')
    })

    it('skips script and style content like the server parser', () => {
        const root = el('<p>visible text</p><script>var hidden = 1;</script><style>.x{}</style>')
        const text = normalizeForSearch(extractSearchableText(root))
        expect(text).toContain('visible text')
        expect(text).not.toContain('hidden')
    })

    it('falls back to textContent for non-DOM objects (test fakes, exotic loaders)', () => {
        expect(extractSearchableText({ textContent: 'plain text' })).toBe('plain text')
        expect(extractSearchableText(null)).toBe('')
    })
})

describe('findTextPosition', () => {
    function el(html) {
        const div = document.createElement('div')
        div.innerHTML = html
        return div
    }

    it('finds a needle that spans inline markup and points into the right text node', () => {
        const root = el('<p>Before. <b>Name</b>: the harbour <i>was</i>quiet tonight.</p>')
        const hit = findTextPosition(root, 'name the harbour was')
        expect(hit.node.nodeValue).toBe('Name')
        expect(hit.offset).toBe(0)
    })

    it('maps an offset inside a node, through punctuation and case', () => {
        const root = el('<p>The Old, Grey Ferryman waited.</p>')
        const hit = findTextPosition(root, 'grey ferryman')
        expect(hit.node.nodeValue).toBe('The Old, Grey Ferryman waited.')
        expect(hit.offset).toBe(9)
    })

    it('does not glue words across a block boundary', () => {
        const root = el('<p>ends here</p><p>starts there</p>')
        expect(findTextPosition(root, 'endsstarts')).toBe(null)
        expect(findTextPosition(root, 'here starts')).not.toBe(null)
    })

    it('returns null when the needle is absent or empty', () => {
        const root = el('<p>nothing to see</p>')
        expect(findTextPosition(root, 'absent words')).toBe(null)
        expect(findTextPosition(root, '')).toBe(null)
        expect(findTextPosition(null, 'x')).toBe(null)
    })

    it('ignores script and style text', () => {
        const root = el('<script>secret words</script><p>plain</p>')
        expect(findTextPosition(root, 'secret words')).toBe(null)
    })
})
