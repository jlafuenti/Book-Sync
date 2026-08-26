/**
 * Text normalization and extraction for the reader's text search — web half
 * of the matcher parity contract (issue #305).
 *
 * The needles the reader searches for are SERVER-derived: sync-point previews
 * extracted by `server/services/epub_parser.py` and matched with
 * `server/services/sync_matcher.py::normalize_for_search` (hand-mirrored in
 * Kotlin as `SyncMatcher.normalizeForSearch`). If the web normalizes or
 * extracts text even slightly differently, real text becomes unfindable —
 * which is exactly how a live audio-rung restore missed a sentence the server
 * proved present, and fell back to the unresolved banner.
 *
 * `normalizeForSearch` here is pinned to the same golden vectors
 * (`server/tests/fixtures/sync_parity/normalize_cases.json`) as the Python
 * and Kotlin implementations — see textSearch.test.js.
 */

// Whitespace variants that must collapse to a plain space BEFORE punctuation
// is stripped. Deleting them instead (the old `[^a-z0-9 ]` fate of a
// non-breaking space) glues the neighbouring words together. Mirrors
// `sync_matcher._WHITESPACE_VARIANTS` / Kotlin `normalizeForSearch`:
// \n \r \t nbsp, en space, em space, thin space, zero-width space,
// narrow no-break space.
export const WHITESPACE_VARIANTS_RE = /[\n\r\t\u00a0\u2002\u2003\u2009\u200b\u202f]/g

// One character of the above, un-anchored — for callers that classify single
// characters (offset mapping in the reader's in-chapter search).
export const WHITESPACE_VARIANT_CHAR_RE = /[\n\r\t\u00a0\u2002\u2003\u2009\u200b\u202f]/

/**
 * Normalize text for matching — semantically identical to the server's
 * `normalize_for_search`: lowercase, all whitespace variants → space, strip
 * everything outside `[a-z0-9 ]`, collapse space runs, trim.
 */
export function normalizeForSearch(text) {
    return String(text ?? '')
        .toLowerCase()
        .replace(WHITESPACE_VARIANTS_RE, ' ')
        .replace(/[^a-z0-9 ]/g, '')
        .replace(/ +/g, ' ')
        .trim()
}

const SKIPPED_ELEMENTS = new Set(['script', 'style', 'head'])

/**
 * Readable text of a DOM subtree, with a separator at EVERY text-node
 * boundary — mirroring the server parser's
 * `BeautifulSoup.get_text(separator="\n")`, which is what the stored
 * sync-point previews were extracted with. `textContent` inserts nothing at
 * tag boundaries, so a sentence spanning inline markup
 * (`<i>Gwendolyn</i>felt …`) glues into "Gwendolynfelt" and the
 * server-derived needle can never match it.
 *
 * Falls back to `.textContent` for objects that aren't walkable DOM nodes
 * (test fakes, exotic loaders) — better glued text than none.
 */
export function extractSearchableText(node) {
    if (!node) return ''
    const parts = []
    const walk = (n) => {
        if (!n) return
        // Text node: its literal value is one part.
        if (n.nodeType === 3) {
            parts.push(n.nodeValue ?? '')
            return
        }
        const name = String(n.nodeName || '').toLowerCase()
        if (SKIPPED_ELEMENTS.has(name)) return
        const children = n.childNodes
        if (!children) return
        for (const child of children) walk(child)
    }
    walk(node)
    if (parts.length === 0) return String(node.textContent ?? '')
    return parts.join('\n')
}
