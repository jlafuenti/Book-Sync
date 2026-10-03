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

// Elements that separate words even though the markup carries no whitespace.
// Mirrors `epub_parser._BLOCK_TAGS` (a block flushes the server's running text
// when it opens and when it closes) plus `br`, which the server treats as
// whitespace. Table cells are in the server's set too, so `<td>x</td><td>y</td>`
// is two words, not "xy".
const BLOCK_ELEMENTS = new Set([
    'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'ul', 'ol', 'dl',
    'dt', 'dd', 'blockquote', 'pre', 'section', 'article', 'aside', 'header',
    'footer', 'nav', 'main', 'figure', 'figcaption', 'table', 'tr', 'td', 'th',
    'caption', 'hr', 'address', 'body', 'html', 'br',
])

/**
 * Walk a DOM subtree the way the server's `_extract_blocks_from_html` reads a
 * document: text nodes are concatenated exactly as they are, so an inline
 * element boundary adds nothing (`<i>W</i>ord` is "Word", `<b>Name</b>: x` is
 * "Name: x" — issue #799), while block-level elements and `<br>` contribute a
 * space so words never glue across them. script/style/head are skipped.
 *
 * Returns `{ raw, segments }`; each segment is a text node with the offset in
 * `raw` where its text starts. `raw` is empty when the node isn't walkable DOM.
 */
function collectSearchableText(root) {
    let raw = ''
    const segments = []
    const walk = (n) => {
        if (!n) return
        if (n.nodeType === 3) {
            const value = n.nodeValue ?? ''
            segments.push({ node: n, start: raw.length })
            raw += value
            return
        }
        const name = String(n.nodeName || '').toLowerCase()
        if (SKIPPED_ELEMENTS.has(name)) return
        const children = n.childNodes
        if (!children) return
        const separates = BLOCK_ELEMENTS.has(name)
        if (separates) raw += ' '
        for (const child of children) walk(child)
        if (separates) raw += ' '
    }
    walk(root)
    return { raw, segments }
}

/**
 * Readable text of a DOM subtree, mirroring the server parser's block text
 * (see `collectSearchableText`). The reader normalizes it with
 * `normalizeForSearch` before comparing against server-derived previews.
 *
 * Falls back to `.textContent` for objects that aren't walkable DOM nodes
 * (test fakes, exotic loaders) — better glued text than none.
 */
export function extractSearchableText(node) {
    if (!node) return ''
    const { raw, segments } = collectSearchableText(node)
    if (segments.length === 0) return String(node.textContent ?? '')
    return raw
}

const KEPT_CHAR_RE = /[a-z0-9 ]/

/**
 * `normalizeForSearch(raw)` plus, for every character of the result, the
 * offset in `raw` it came from. Needed because normalization drops and
 * collapses characters, so an index into the normalized text says nothing
 * about where to put a DOM range.
 */
function normalizeWithMap(raw) {
    let text = ''
    const map = []
    for (let i = 0; i < raw.length; i++) {
        for (const lowered of raw[i].toLowerCase()) {
            const ch = WHITESPACE_VARIANT_CHAR_RE.test(lowered) ? ' ' : lowered
            if (!KEPT_CHAR_RE.test(ch)) continue
            if (ch === ' ' && (text === '' || text.endsWith(' '))) continue
            text += ch
            map.push(i)
        }
    }
    return { text, map }
}

/**
 * Find the first occurrence of `needle` (already passed through
 * `normalizeForSearch`) in the searchable text of `root`, and return the text
 * node and character offset where it starts — `{ node, offset }` — or null.
 * Uses the same walk as `extractSearchableText`, so it finds exactly the text
 * the server-derived needle was cut from.
 */
export function findTextPosition(root, needle) {
    if (!root || !needle) return null
    const { raw, segments } = collectSearchableText(root)
    if (segments.length === 0) return null
    const { text, map } = normalizeWithMap(raw)
    const idx = text.indexOf(needle)
    if (idx < 0) return null
    const rawIdx = map[idx]
    for (const seg of segments) {
        const length = (seg.node.nodeValue ?? '').length
        if (rawIdx >= seg.start && rawIdx < seg.start + length) {
            return { node: seg.node, offset: rawIdx - seg.start }
        }
    }
    return null
}
