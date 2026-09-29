/**
 * Rolldown `output.codeSplitting` for the web build (issues #281, #516).
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

// Rolldown's CommonJS interop helpers (its RUNTIME_MODULE_ID). Every package
// above is CommonJS, so the epubjs chunk imports them. Unplaced, Rolldown puts
// them in a common chunk with app code shared by the entry and lazy routes
// (api.js on the first Vite 8 build), and epubjs's hash would then follow every
// edit to that code. In a chunk of their own they change only with Rolldown.
const ROLLDOWN_RUNTIME_RE = /^\0rolldown\/runtime\.js$/

// Vite 8 bundles with Rolldown, where this replaces Rollup's `manualChunks`
// function (still accepted there, but deprecated). A group's `test` claims a
// module and, by default, the modules it imports; every epub.js dependency is
// in the list anyway, so the chunk holds the same set either way. Groups are
// tried in order.
export const codeSplitting = {
    groups: [
        { name: 'rolldown-runtime', test: ROLLDOWN_RUNTIME_RE },
        { name: 'epubjs', test: EPUBJS_RE },
    ],
}
