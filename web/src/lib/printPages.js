/**
 * Print-page-list mode (issue #730, task 7): the printed book's page numbers,
 * read from an EPUB's embedded page list (EPUB 3 `nav epub:type="page-list"`
 * or EPUB 2 NCX `pageList`). epub.js (0.3.93) parses either into
 * `book.pageList.pageList`, an array of `{ href, page }` in book order, where
 * `page = parseInt(label)` — a roman numeral becomes NaN.
 *
 * Task 8 wires `pageListEntries` + `printListAt` + `fragmentBeforeOrAt` into
 * the reader and feeds the result to `resolvePageLabel`'s `printList` shape
 * (`lib/readerProgress.js`): `{ currentLabel, firstLabel, lastLabel }`.
 *
 * Pure except `fragmentBeforeOrAt`, which reads the rendered DOM the same way
 * `EbookReader`'s `relocated` handler already does for spine matching.
 */
// The package's public CFI class, not a path inside it (issue #736).
import ePub from 'epubjs'

/**
 * Resolve `book.pageList.pageList` into `{ sectionIndex, fragment, label }`,
 * in book order, dropping entries with a non-finite page or an href that
 * doesn't map to any spine item.
 *
 * `href` may carry a `#fragment`; the part before it is matched against
 * `spineHrefs` by `spineIndexForPath`: an exact path, else a suffix match
 * (either side ending in `/` + the other) when only one spine item fits.
 *
 * @param {{ pageList?: { pageList?: Array<{ href?: string, page?: number }> } }} book
 * @param {string[]} spineHrefs
 * @returns {Array<{ sectionIndex: number, fragment: string, label: string }>}
 */
// An exact spine path wins; otherwise the suffix rule, but only when exactly
// one spine item fits - `ch1.xhtml` against `a/ch1.xhtml` and `b/ch1.xhtml` is
// ambiguous and resolves to none rather than to whichever came first (#736).
export function spineIndexForPath(path, spineHrefs) {
    if (!path) return -1
    const exact = spineHrefs.indexOf(path)
    if (exact !== -1) return exact
    let found = -1
    for (let i = 0; i < spineHrefs.length; i++) {
        const sh = spineHrefs[i]
        if (sh && (path.endsWith('/' + sh) || sh.endsWith('/' + path))) {
            if (found !== -1) return -1
            found = i
        }
    }
    return found
}

export function pageListEntries(book, spineHrefs) {
    const raw = book?.pageList?.pageList
    if (!Array.isArray(raw) || !Array.isArray(spineHrefs)) return []

    const entries = []
    for (const item of raw) {
        const page = item?.page
        if (!Number.isFinite(page)) continue

        const href = typeof item?.href === 'string' ? item.href : ''
        const hashIndex = href.indexOf('#')
        const path = hashIndex === -1 ? href : href.slice(0, hashIndex)
        const fragment = hashIndex === -1 ? '' : href.slice(hashIndex + 1)

        const sectionIndex = spineIndexForPath(path, spineHrefs)
        if (sectionIndex === -1) continue

        entries.push({ sectionIndex, fragment, label: String(page) })
    }
    return entries
}

/**
 * The printList object `resolvePageLabel` expects, computed at a reading
 * position identified by `sectionIndex` (current spine index) plus
 * `isBeforeOrAt(fragment)`, which answers "at or before the position" for an
 * entry's fragment — but only for entries in the current section (an entry in
 * any earlier section always qualifies; a later section never does).
 *
 * `currentLabel` is the label of the last qualifying entry in book order, or
 * `null` when none qualifies. `firstLabel`/`lastLabel` are the first and last
 * labels regardless of position. `null` overall when there are no entries.
 *
 * @param {Array<{ sectionIndex: number, fragment: string, label: string }>} entries
 * @param {number} sectionIndex
 * @param {(fragment: string) => boolean} isBeforeOrAt
 * @returns {{ currentLabel: string|null, firstLabel: string, lastLabel: string } | null}
 */
export function printListAt(entries, sectionIndex, isBeforeOrAt) {
    if (!Array.isArray(entries) || entries.length === 0) return null

    let currentLabel = null
    for (const entry of entries) {
        if (entry.sectionIndex < sectionIndex) {
            currentLabel = entry.label
        } else if (entry.sectionIndex === sectionIndex && isBeforeOrAt(entry.fragment)) {
            currentLabel = entry.label
        }
    }

    return {
        currentLabel,
        firstLabel: entries[0].label,
        lastLabel: entries[entries.length - 1].label,
    }
}

/**
 * Whether a page-list marker (`fragment`, an element id in the current
 * section) sits at or before the reading position (`startCfi`). Finds the
 * element in the rendered `contents.document`, builds its CFI with
 * `section.cfiFromElement`, and compares it against `startCfi` — the same
 * comparison epub.js's own `Locations`/`PageList` code uses. A missing
 * element (a stale or malformed page-list target) is `false`, not a thrown
 * error.
 *
 * @param {{ document?: Document }} contents
 * @param {string} fragment
 * @param {string} startCfi
 * @param {{ cfiFromElement: (el: Element) => string }} section
 * @returns {boolean}
 */
export function fragmentBeforeOrAt(contents, fragment, startCfi, section) {
    const doc = contents?.document
    const el = doc && fragment ? doc.getElementById(fragment) : null
    if (!el) return false

    const elCfi = section.cfiFromElement(el)
    return new ePub.CFI().compare(elCfi, startCfi) <= 0
}
