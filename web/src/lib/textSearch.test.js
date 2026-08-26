import { describe, it, expect } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import { normalizeForSearch, extractSearchableText } from './textSearch'

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
    // Server previews are extracted with BeautifulSoup get_text(separator="\n")
    // — a separator at EVERY tag boundary. textContent inserts nothing, so a
    // sentence spanning inline markup glued into "gwendolynfelt" and the
    // server-derived needle could never match. The extractor must mirror the
    // server's separator semantics.

    function el(html) {
        const div = document.createElement('div')
        div.innerHTML = html
        return div
    }

    it('separates text nodes at tag boundaries like the server parser', () => {
        const root = el('<p>And then <i>Gwendolyn</i>felt herself <em>smile</em>slightly. More.</p>')
        expect(normalizeForSearch(extractSearchableText(root)))
            .toContain('gwendolyn felt herself smile slightly')
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
