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

// React proper. `react-router-dom` and friends are left alone on purpose:
// the group exists to empty the common chunk of React, not to curate vendors.
const REACT_RE = /[\\/]node_modules[\\/](react|react-dom|scheduler)[\\/]/

// The API client: the `src/api.js` barrel and the `src/api/` modules behind it.
const API_RE = /[\\/]src[\\/]api(\.js$|[\\/])/

// Vite 8 bundles with Rolldown, where this replaces Rollup's `manualChunks`
// function (still accepted there, but deprecated). A group's `test` claims a
// module and, by default, the modules it imports; every epub.js dependency is
// in the list anyway, so the chunk holds the same set either way.
//
// The react-vendor and app-api groups protect the epubjs one (issue #516).
// epub.js is CommonJS, so its chunk imports Rolldown's interop runtime, and
// Rolldown puts that runtime in the common chunk shared by the entry and the
// lazy routes. Left to itself, that chunk holds React and the API client, so
// any edit to the client rehashed epub.js and every installed PWA downloaded
// it again. With both in groups of their own, Rolldown 1.2.12 emits the
// runtime as a separate ~1 KB chunk and epub.js imports only that. This is
// observed behaviour, not a documented guarantee, and a group matching the
// runtime directly is ignored: after a Rolldown bump, build and check which
// chunk `epubjs-*.js` imports from (docs/web-pwa.md).
export const codeSplitting = {
    groups: [
        { name: 'epubjs', test: EPUBJS_RE },
        { name: 'react-vendor', test: REACT_RE },
        { name: 'app-api', test: API_RE },
    ],
}
