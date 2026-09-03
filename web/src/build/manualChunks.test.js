import { describe, it, expect } from 'vitest'
import { manualChunks } from './manualChunks'

/**
 * Vendor chunking (issue #281).
 *
 * The service worker precaches every built `.js` file by content hash and
 * re-downloads whatever changed on every deploy. epub.js and its dependency
 * tree (jszip, localforage, lodash, core-js, …) are a large, near-frozen slice
 * of the bundle — but while they sit inside the entry chunk, a one-line change
 * to any eager page rehashes them and every installed PWA fetches them again.
 *
 * This does not shrink the first install; it makes every later one small.
 * `EbookReader` is imported by HomePage, which stays eager on purpose (first
 * paint), so pulling the vendor tree out is the lever available without making
 * the reader itself lazy.
 *
 * The mapping function is tested rather than the built output: the repo does
 * not build in CI, so a test that shelled out to `vite build` would never run.
 */

const nm = (pkg) => `/repo/web/node_modules/${pkg}/dist/index.js`

describe('manualChunks (issue #281)', () => {
    it('puts epub.js and its dependency tree in one long-lived chunk', () => {
        for (const pkg of [
            'epubjs', 'jszip', 'localforage', 'lodash', 'core-js',
            '@xmldom/xmldom', 'marks-pane', 'path-webpack', 'event-emitter',
        ]) {
            expect(manualChunks(nm(pkg)), pkg).toBe('epubjs')
        }
    })

    it('leaves react and the rest of node_modules where Rollup put them', () => {
        expect(manualChunks(nm('react-dom'))).toBeUndefined()
        expect(manualChunks(nm('react-router-dom'))).toBeUndefined()
        expect(manualChunks(nm('react-markdown'))).toBeUndefined()
    })

    it('never claims application source', () => {
        expect(manualChunks('/repo/web/src/pages/HomePage.jsx')).toBeUndefined()
        // A source path that merely mentions a vendor name must not match.
        expect(manualChunks('/repo/web/src/lib/epubjs-helpers.js')).toBeUndefined()
    })

    it('matches on Windows-style separators too', () => {
        expect(manualChunks('C:\\repo\\web\\node_modules\\epubjs\\lib\\epub.js')).toBe('epubjs')
    })
})
