// Shared epub.js rendition settings (issue #730): the reader and the future
// off-screen page counter (Task 6) must lay out identically, so both read
// these constants rather than each keeping their own copy.

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
