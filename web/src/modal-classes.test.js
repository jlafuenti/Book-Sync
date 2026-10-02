import { describe, it, expect } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * Every class a call site hands to `<Modal className=...>` or `overlayClassName=...`
 * must be styled somewhere (issue #783).
 *
 * `Modal` defaults `className` to `.modal`, which is where the panel (background,
 * border, padding, width) comes from. Passing a class replaces that default, so a
 * class no stylesheet defines leaves the dialog with no panel at all: four
 * confirmations passed `modal-dialog`, which never existed, and drew as a bare strip
 * of text and buttons across the middle of the page. jsdom computes no CSS, so no
 * rendering test could see it; this checks the class names against the stylesheets.
 */

const SRC = path.dirname(fileURLToPath(import.meta.url))

function walk(dir, out = []) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name)
        if (entry.isDirectory()) walk(full, out)
        else out.push(full)
    }
    return out
}

const files = walk(SRC)
const css = files.filter((f) => f.endsWith('.css')).map((f) => fs.readFileSync(f, 'utf8')).join('\n')
const sources = files.filter((f) => /\.jsx?$/.test(f) && !/\.test\.jsx?$/.test(f))

function modalClassUses() {
    const uses = []
    for (const file of sources) {
        const text = fs.readFileSync(file, 'utf8')
        // `=>` is skipped over: an inline `onClose={() => ...}` would otherwise end the tag
        // before the className that follows it.
        for (const tag of text.matchAll(/<Modal\b(?:=>|[^>])*>/gs)) {
            for (const attr of tag[0].matchAll(/\b(className|overlayClassName)="([^"]+)"/g)) {
                for (const cls of attr[2].split(/\s+/).filter(Boolean)) {
                    uses.push({ file: path.relative(SRC, file), cls })
                }
            }
        }
    }
    return uses
}

describe('Modal class names', () => {
    it('finds the call sites it is meant to check', () => {
        expect(modalClassUses().length).toBeGreaterThan(5)
    })

    it('every class passed to Modal is defined in a stylesheet', () => {
        const missing = modalClassUses().filter(({ cls }) => !new RegExp(`\\.${cls}(?![\\w-])`).test(css))
        expect(missing).toEqual([])
    })
})
