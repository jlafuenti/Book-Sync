// Shared epub.js rendition settings (issue #730): the reader and the future
// off-screen page counter (Task 6) must lay out identically, so both read
// these constants rather than each keeping their own copy.

// Changing RENDITION_OPTIONS or READER_THEME_RULES changes page counts:
// bump the `tandem_page_counts_v1` key in pageCountCache.js when you do.

// Passed to `book.renderTo(...)`.
export const RENDITION_OPTIONS = {
    width: '100%',
    height: '100%',
    flow: 'paginated',
    spread: 'none',
}

// Structural styles only — colors come from the injected
// #tandem-reader-theme <style>, so the palette can change live with the
// reader-theme picker (issue #57). Passed to `rendition.themes.default(...)`.
export const READER_THEME_RULES = {
    'body': {
        'font-family': 'Georgia, "Times New Roman", serif !important',
        'padding': '0 48px !important',
        'max-width': '100% !important',
        'box-sizing': 'border-box !important',
    },
    'img': { 'max-width': '100% !important' },
    '*': { 'max-width': '100% !important', 'box-sizing': 'border-box !important' },
}

// The <style id="tandem-font-size"> content, injected per chapter document
// (CSS vars set on the parent document don't cross into the epub iframe).
export function fontSizeCss(pct) {
    return `html { font-size: ${pct}% !important; }`
}

// Destroy an epub.js Book, and remove the window listener its rendition leaves
// behind (issue #736): epub.js 0.3.93's Stage adds "orientationchange" but its
// destroy() removes "orientationChange", so every reader open and every page
// count left one handler on window. Never throws.
export function destroyBook(book) {
    if (!book) return
    const onOrientation = book.rendition?.manager?.stage?.orientationChangeFunc
    try { book.destroy() } catch { /* already gone */ }
    if (onOrientation) window.removeEventListener('orientationchange', onOrientation, false)
}
