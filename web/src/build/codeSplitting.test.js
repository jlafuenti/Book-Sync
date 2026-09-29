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

// Rolldown's RUNTIME_MODULE_ID: the CommonJS interop helpers every CJS package
// (epub.js's whole tree) is wrapped with.
const RUNTIME = '\0rolldown/runtime.js'

describe('codeSplitting (issues #281, #516)', () => {
    it('declares the runtime and epubjs groups, runtime first', () => {
        // Groups are tried in order; the runtime must be claimed before
        // anything broader could match it.
        expect(codeSplitting.groups.map(g => g.name)).toEqual(['rolldown-runtime', 'epubjs'])
    })

    it('gives the Rolldown runtime a chunk of its own', () => {
        // Left alone, Rolldown puts the helpers in a common chunk alongside
        // app code shared by the entry and lazy routes (api.js, measured on the
        // first Vite 8 build). epubjs imports the helpers, so its hash would
        // then follow every edit to that app code.
        expect(groupFor(RUNTIME)).toBe('rolldown-runtime')
        expect(groupFor('/repo/web/node_modules/rolldown/dist/index.mjs')).toBeUndefined()
        expect(groupFor('/repo/web/src/rolldown/runtime.js')).toBeUndefined()
    })

    it('puts epub.js and its dependency tree in one long-lived chunk', () => {
        for (const pkg of [
            'epubjs', 'jszip', 'localforage', 'lodash', 'core-js',
            '@xmldom/xmldom', 'marks-pane', 'path-webpack', 'event-emitter',
        ]) {
            expect(groupFor(nm(pkg)), pkg).toBe('epubjs')
        }
    })

    it('leaves react and the rest of node_modules where Rolldown puts them', () => {
        expect(groupFor(nm('react-dom'))).toBeUndefined()
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
        for (const { test } of codeSplitting.groups) {
            expect(test.global || test.sticky).toBe(false)
        }
    })
})
