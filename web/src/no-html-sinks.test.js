import { describe, it, expect } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'

/**
 * No HTML-injection sinks in web/src (issue #286).
 *
 * The 30-day refresh JWT lives in localStorage, readable by any script on the
 * origin. That is an acceptable trade **only** while the origin has nowhere to
 * inject HTML — and nothing in the repo said so, which made it a property
 * nobody knew they were maintaining.
 *
 * The realistic path to breaking it is not malice. ABS book descriptions
 * currently render their literal `<p>` tags in BookDetailPage; the obvious fix
 * is `dangerouslySetInnerHTML` or `rehype-raw`, and either one turns a cosmetic
 * improvement into account takeover for anyone whose library metadata an
 * attacker can influence.
 *
 * `web/` has no ESLint config at all, so this test is the cheap executable form
 * of the rule written up in CLAUDE.md. If ESLint is ever adopted, `react/no-danger`
 * plus a `no-restricted-imports` entry for `rehype-raw` replaces it.
 *
 * **If you are here because this test failed:** the fix is not to add an
 * exception. It is to move the tokens to httpOnly cookies with CSRF protection
 * first, and then relax this rule in the same change.
 */

const SINKS = [
    'dangerouslySetInnerHTML',
    'innerHTML',
    'outerHTML',
    'insertAdjacentHTML',
    'document.write',
    'rehype-raw',
]

/** Source files only — tests legitimately build DOM fixtures with innerHTML. */
function sourceFiles(dir, acc = []) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name)
        if (entry.isDirectory()) {
            sourceFiles(full, acc)
        } else if (/\.(js|jsx)$/.test(entry.name) && !/\.test\.(js|jsx)$/.test(entry.name)) {
            acc.push(full)
        }
    }
    return acc
}

describe('web/src has no HTML-injection sinks (issue #286)', () => {
    const root = path.join(process.cwd(), 'src')

    it('finds source files to scan', () => {
        // A silently-empty walk would make the assertion below pass for the
        // wrong reason — the same way an empty scan made the Android host guard
        // look healthy while reading nothing.
        const files = sourceFiles(root)
        expect(files.length).toBeGreaterThan(20)
    })

    it('uses none of the sinks that would expose the localStorage tokens', () => {
        const offenders = []
        for (const file of sourceFiles(root)) {
            const src = fs.readFileSync(file, 'utf8')
            src.split(/\r?\n/).forEach((line, i) => {
                const code = line.trim()
                if (code.startsWith('//') || code.startsWith('*') || code.startsWith('/*')) return
                for (const sink of SINKS) {
                    if (code.includes(sink)) {
                        offenders.push(`${path.relative(root, file)}:${i + 1} -> ${sink}`)
                    }
                }
            })
        }

        expect(
            offenders,
            'These expose the refresh token in localStorage to any injected script. ' +
            'See the Authentication section of CLAUDE.md before changing this rule.',
        ).toEqual([])
    })
})
