import { describe, it, expect } from 'vitest'
import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

// Issue #178: the Content-Security-Policy in Caddyfile.example allows exactly
// one inline script — the theme bootstrap in index.html — by its sha256 hash.
// Editing that script (even a comment or an indent) changes the hash, and an
// enforcing CSP would then block it: the page would open in the wrong theme
// and the PWA theme-color would stay stale. This test fails first, so the
// hash gets updated in the same change.
//
// Vite copies a classic inline script into the built index.html verbatim, so
// hashing the source is hashing what the browser sees. CRLF is normalised
// because a Windows checkout with core.autocrlf has \r\n on disk while the
// build (and git) serve \n.
const here = dirname(fileURLToPath(import.meta.url))
const indexHtml = readFileSync(resolve(here, '../../index.html'), 'utf-8').replace(/\r\n/g, '\n')
const caddyfile = readFileSync(resolve(here, '../../../Caddyfile.example'), 'utf-8')

const scripts = [...indexHtml.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)]
    .map(([, attrs, body]) => ({ attrs, body }))
const inlineScripts = scripts.filter((s) => !/\bsrc=/.test(s.attrs))

const cspLine = caddyfile
    .split('\n')
    .find((line) => /^\s*Content-Security-Policy(-Report-Only)?\s+"/.test(line))

function directive(name) {
    const clause = cspLine
        .match(/"([^"]+)"/)[1]
        .split(';')
        .map((c) => c.trim().split(/\s+/))
        .find((parts) => parts[0] === name)
    return clause ? clause.slice(1) : null
}

describe('CSP inline-script hash (Caddyfile.example vs index.html)', () => {
    it('index.html has exactly one inline script, the theme bootstrap', () => {
        expect(inlineScripts).toHaveLength(1)
        expect(inlineScripts[0].body).toContain('tandem_theme')
        // The app itself is the one module script; anything else inline would
        // need its own hash in the policy.
        expect(scripts.filter((s) => /type="module"/.test(s.attrs))).toHaveLength(1)
    })

    it('the policy in Caddyfile.example carries that script’s sha256', () => {
        expect(cspLine, 'Caddyfile.example has no Content-Security-Policy line').toBeDefined()
        const digest = createHash('sha256').update(inlineScripts[0].body, 'utf8').digest('base64')
        const expected = `'sha256-${digest}'`
        const scriptSrc = directive('script-src')
        expect(scriptSrc, 'CSP has no script-src directive').not.toBeNull()
        expect(
            scriptSrc,
            `script-src does not allow the inline theme bootstrap. The script in ` +
                `web/index.html changed; replace the sha256 in Caddyfile.example with ${expected}`,
        ).toContain(expected)
        // No stale hash left behind from an earlier version of the script.
        expect(scriptSrc.filter((s) => s.startsWith("'sha256-"))).toEqual([expected])
        expect(scriptSrc).not.toContain("'unsafe-inline'")
    })
})
