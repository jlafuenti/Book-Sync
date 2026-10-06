import { describe, it, expect } from 'vitest'
import { codeSplitting } from './codeSplitting'

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
 * Vite 8 bundles with Rolldown (issue #516), whose `output.codeSplitting`
 * groups replace Rollup's `manualChunks`. The group is tested rather than the
 * built output: the repo does not build in CI, so a test that shelled out to
 * `vite build` would never run.
 */

const nm = (pkg) => `/repo/web/node_modules/${pkg}/dist/index.js`

const groupFor = (id) => codeSplitting.groups.find(g => g.test.test(id))?.name

describe('codeSplitting (issues #281, #516)', () => {
    it('declares the epubjs, react-vendor and app-api groups', () => {
        expect(codeSplitting.groups.map(g => g.name)).toEqual(['epubjs', 'react-vendor', 'app-api'])
    })

    it('puts React itself in its own long-lived chunk', () => {
        // Without this, Rolldown's CommonJS interop runtime — which the epubjs
        // chunk imports — lands in a common chunk beside React and the API
        // client, and any API edit rehashes epub.js (issue #516).
        for (const pkg of ['react', 'react-dom', 'scheduler']) {
            expect(groupFor(nm(pkg)), pkg).toBe('react-vendor')
        }
        expect(groupFor('/repo/web/node_modules/react/jsx-runtime.js')).toBe('react-vendor')
    })

    it('puts the API client, barrel and modules, in its own chunk', () => {
        expect(groupFor('/repo/web/src/api.js')).toBe('app-api')
        expect(groupFor('/repo/web/src/api/http.js')).toBe('app-api')
        expect(groupFor('C:\\repo\\web\\src\\api\\library.js')).toBe('app-api')
    })

    it('does not mistake look-alike paths for the API client', () => {
        expect(groupFor('/repo/web/src/lib/api.js')).toBeUndefined()
        expect(groupFor('/repo/web/src/api.test.js')).toBeUndefined()
        expect(groupFor('/repo/web/src/apiHelpers.js')).toBeUndefined()
    })

    it('puts epub.js and its dependency tree in one long-lived chunk', () => {
        for (const pkg of [
            'epubjs', 'jszip', 'localforage', 'lodash', 'core-js',
            '@xmldom/xmldom', 'marks-pane', 'path-webpack', 'event-emitter',
        ]) {
            expect(groupFor(nm(pkg)), pkg).toBe('epubjs')
        }
    })

    it('leaves the rest of node_modules where Rolldown puts them', () => {
        expect(groupFor(nm('react-router-dom'))).toBeUndefined()
        expect(groupFor(nm('react-markdown'))).toBeUndefined()
    })

    it('never claims application source', () => {
        expect(groupFor('/repo/web/src/pages/HomePage.jsx')).toBeUndefined()
        // A source path that merely mentions a vendor name must not match.
        expect(groupFor('/repo/web/src/lib/epubjs-helpers.js')).toBeUndefined()
    })

    it('matches on Windows-style separators too', () => {
        expect(groupFor('C:\\repo\\web\\node_modules\\epubjs\\lib\\epub.js')).toBe('epubjs')
    })

    it('uses stateless regexes, so repeated tests give the same answer', () => {
        // A /g or /y flag would make RegExp.test advance lastIndex between calls.
        for (const { name, test } of codeSplitting.groups) {
            expect(test.global || test.sticky, name).toBe(false)
        }
    })
})
