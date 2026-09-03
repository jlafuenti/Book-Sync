import { describe, it, expect } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'

/**
 * epub.js stays on 0.3.x (issue #285).
 *
 * `npm audit` reports six high-severity `@xmldom/xmldom` advisories reached
 * through `epubjs@0.3.93`, and the only fix npm offers is `epubjs@0.4.2` —
 * flagged `isSemVerMajor`, an old alpha, and the library that renders every
 * book in the app. That trade is not worth taking for advisories that cannot
 * fire here:
 *
 *   - `epubjs/src/utils/core.js` only reaches for xmldom when
 *     `typeof DOMParser === "undefined" || forceXMLDom`;
 *   - `epubjs/src/section.js` only when
 *     `typeof XMLSerializer === "undefined" || isIE`.
 *
 * We ship to browsers, which have both, and nothing sets `forceXMLDom`. The
 * advisories are allow-listed in `web/audit-ci.jsonc` with that reasoning.
 *
 * This test is the other half of that decision: it stops a Dependabot PR (or a
 * stray `npm audit fix --force`) from silently taking the major to make the
 * audit output quiet. Upgrading epub.js is a real change — do it deliberately,
 * with a reader regression pass, and update this test in the same PR.
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
