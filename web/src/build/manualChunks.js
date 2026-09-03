/**
 * Rollup `output.manualChunks` for the web build (issue #281).
 *
 * Build-time only — this module is imported by `vite.config.js`, never by the
 * app. It lives under `src/` so the unit test next to it runs in the normal
 * vitest suite; importing `vite.config.js` from a test drags esbuild into the
 * jsdom environment and fails to load.
 *
 * epub.js and everything it pulls in are the one large, effectively frozen
 * slice of vendor code in the bundle. `EbookReader` is imported by HomePage,
 * which is deliberately eager (first paint), so route-level splitting cannot
 * move it: without this it rides in the entry chunk and gets a fresh content
 * hash — and therefore a fresh download for every installed PWA — on any
 * deploy that touches any eager page.
 */

/** Packages that only ever arrive via epub.js. Keep in sync with its deps. */
const EPUBJS_TREE = [
    'epubjs',
    '@xmldom/xmldom',
    'core-js',
    'event-emitter',
    'jszip',
    'localforage',
    'lodash',
    'marks-pane',
    'path-webpack',
]

// Anchored on the node_modules boundary so an application file that merely
// mentions a package name (src/lib/epubjs-helpers.js) is never claimed.
const EPUBJS_RE = new RegExp(
    `[\\\\/]node_modules[\\\\/](${EPUBJS_TREE.map(p => p.replace('/', '[\\\\/]')).join('|')})[\\\\/]`,
)

export function manualChunks(id) {
    if (EPUBJS_RE.test(id)) return 'epubjs'
    return undefined
}
