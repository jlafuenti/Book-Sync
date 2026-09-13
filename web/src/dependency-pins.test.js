import { describe, it, expect } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'

/**
 * epub.js stays on 0.3.x, and its XML parser stays patched (issues #285, #447).
 *
 * `epubjs@0.3.93` caps `@xmldom/xmldom` at `^0.7.5`, which carries a family of
 * XML-serialization advisories. The only fix npm offers is `epubjs@0.4.2` — an
 * old alpha, flagged `isSemVerMajor`, whose dependency is the *abandoned
 * unscoped* `xmldom` with a critical advisory and no fixed version. That is
 * worse, not better (#447 reverted an automatic bump to it).
 *
 * So the library stays on 0.3.x and an npm `overrides` entry lifts the
 * transitive `@xmldom/xmldom` to 0.8.15, where the advisories are fixed.
 *
 * Until the release that shipped this, `web/audit-ci.jsonc` also allow-listed
 * those six advisories — written before the override existed, and left behind
 * by it. An allow-list entry for a version no longer installed is worse than
 * none: were the override ever dropped, the vulnerable parser would come back
 * and the audit gate would stay silent. The entries are removed, and the tests
 * below pin the override that made them unnecessary.
 *
 * Upgrading epub.js is a real change — do it deliberately, with a reader
 * regression pass, and update this test in the same PR.
 */

const pkg = JSON.parse(
    fs.readFileSync(path.join(process.cwd(), 'package.json'), 'utf8'),
)

describe('dependency pins', () => {
    it('keeps epubjs on 0.3.x', () => {
        const range = pkg.dependencies?.epubjs
        expect(range, 'epubjs is no longer a direct dependency').toBeTruthy()
        expect(
            range.startsWith('^0.3.') || range.startsWith('~0.3.') || range.startsWith('0.3.'),
            `epubjs is pinned to "${range}"; see the note above before moving off 0.3.x`,
        ).toBe(true)
    })

    it('lifts @xmldom/xmldom past the advisories with an override', () => {
        // Without this entry epubjs 0.3.93 resolves @xmldom/xmldom ^0.7.5, the
        // vulnerable range. The audit allow-list that used to hide that is gone,
        // so dropping the override now fails CI rather than passing quietly.
        const range = pkg.overrides?.['@xmldom/xmldom']
        expect(range, 'package.json has no @xmldom/xmldom override').toBeTruthy()
        const [major, minor, patch] = range.replace(/^[\^~]/, '').split('.').map(Number)
        expect(
            major > 0 || minor > 8 || (minor === 8 && patch >= 15),
            `@xmldom/xmldom is overridden to "${range}"; it must stay at or above 0.8.15`,
        ).toBe(true)
    })

    it('installs a patched @xmldom/xmldom', () => {
        const installed = JSON.parse(
            fs.readFileSync(
                path.join(process.cwd(), 'node_modules', '@xmldom', 'xmldom', 'package.json'),
                'utf8',
            ),
        )
        const [major, minor, patch] = installed.version.split('.').map(Number)
        expect(major > 0 || minor > 8 || (minor === 8 && patch >= 15)).toBe(true)
    })

    it('resolves epubjs to a 0.3.x install', () => {
        const installed = JSON.parse(
            fs.readFileSync(
                path.join(process.cwd(), 'node_modules', 'epubjs', 'package.json'),
                'utf8',
            ),
        )
        expect(installed.version.startsWith('0.3.')).toBe(true)
    })
})
