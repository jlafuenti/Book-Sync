import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { THEMES, THEME_SLUGS, DEFAULT_THEME, READER_MODES, getReaderPalette, applyTheme } from './themes'

// Issue #57: the ebook reader used to hardcode a copy of Blueprint's palette.
// getReaderPalette is the single source of the reader's iframe colors: 'match'
// derives from the active app theme, light/sepia/dark are fixed reading modes
// (mirroring Android's reader_display prefs).
describe('getReaderPalette', () => {
    it('match mode derives from the active app theme vars', () => {
        for (const slug of THEME_SLUGS) {
            const vars = THEMES[slug].vars
            expect(getReaderPalette('match', slug)).toEqual({
                background: vars['--bg-primary'],
                text: vars['--text-primary'],
                link: vars['--accent-secondary'],
            })
        }
    })

    it('match mode falls back to the default theme for an unknown slug', () => {
        const vars = THEMES[DEFAULT_THEME].vars
        expect(getReaderPalette('match', 'no-such-theme')).toEqual({
            background: vars['--bg-primary'],
            text: vars['--text-primary'],
            link: vars['--accent-secondary'],
        })
        expect(getReaderPalette('match', undefined)).toEqual(getReaderPalette('match', DEFAULT_THEME))
    })

    it('light and sepia are fixed reading palettes', () => {
        const light = getReaderPalette('light', 'ember')
        const sepia = getReaderPalette('sepia', 'ember')
        expect(light).toEqual({ background: '#fafaf7', text: '#1c1b22', link: '#6d28d9' })
        expect(sepia).toEqual({ background: '#f4ecd8', text: '#5b4636', link: '#8b5e34' })
        // Fixed means fixed: the app theme must not leak in.
        expect(getReaderPalette('light', 'aurora')).toEqual(light)
    })

    it('dark is the pre-#57 hardcoded reader palette, independent of app theme', () => {
        expect(getReaderPalette('dark', 'forest-night')).toEqual({
            background: '#0f0f1a', text: '#e8e8f0', link: '#a78bfa',
        })
    })

    it('an unknown mode behaves like match (the default)', () => {
        expect(getReaderPalette('bogus', 'slate')).toEqual(getReaderPalette('match', 'slate'))
    })

    it('READER_MODES lists the four selectable modes', () => {
        expect(READER_MODES).toEqual(['match', 'light', 'sepia', 'dark'])
    })
})

// Issue #62: when installed as a PWA the OS paints its chrome (status bar,
// task switcher) with <meta name="theme-color">, so a theme change has to
// move it along with the CSS variables and the favicon.
describe('applyTheme', () => {
    it('sets the theme-color meta to the theme background', () => {
        document.head.innerHTML = '<meta name="theme-color" content="#000000"><link id="favicon" href="/x.svg">'
        applyTheme('ember')
        expect(document.querySelector('meta[name="theme-color"]').getAttribute('content'))
            .toBe(THEMES.ember.vars['--bg-primary'])
        expect(document.getElementById('favicon').getAttribute('href')).toBe(THEMES.ember.favicon)
    })

    it('does not mind a page without the meta tag', () => {
        document.head.innerHTML = ''
        expect(() => applyTheme('slate')).not.toThrow()
    })
})

// ---------------------------------------------------------------------------
// Issue #280: a var(--x) whose custom property is declared nowhere makes the
// whole declaration invalid at computed-value time, so that background, border
// or colour silently does not render. Most use sites are inline `style={{…}}`
// objects, which no linter and no build step looks at — sixteen of them had
// drifted to names this app never declared (`--surface`, `--primary`, `--text`,
// `--border-color`, `--bg`, `--bg-color`, `--bg-hover`).
//
// The scan below is the durable half of the fix: the next such typo fails here
// instead of showing up as a missing border in a screenshot months later.
describe('CSS custom properties', () => {
    const SRC = resolve(dirname(fileURLToPath(import.meta.url)))

    // A declaration is `--name:` at the start of a rule body or after a
    // semicolon; that shape never matches the `--name` inside `var(--name)`.
    const CSS_DECL = /(?:^|[;{])\s*(--[\w-]+)\s*:/gm
    // Custom properties set from JS: a quoted key in a style object
    // (`style={{ '--swatch-color': … }}`, the `vars` maps in themes.js) or a
    // setProperty() call.
    const JS_DECL = /["'](--[\w-]+)["']\s*:|setProperty\(\s*["'](--[\w-]+)["']/g
    const USE = /var\(\s*(--[\w-]+)/g

    function sourceFiles(dir) {
        return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
            const full = join(dir, entry.name)
            if (entry.isDirectory()) return sourceFiles(full)
            if (/\.test\.[jt]sx?$/.test(entry.name)) return []   // fixtures, not shipped styles
            return /\.(css|jsx?)$/.test(entry.name) ? [full] : []
        })
    }

    const files = sourceFiles(SRC)

    function collect(re, groups = [1]) {
        const found = new Map()
        for (const file of files) {
            const text = readFileSync(file, 'utf-8')
            for (const match of text.matchAll(re)) {
                for (const group of groups) {
                    if (match[group]) found.set(match[group], (found.get(match[group]) || []).concat(file))
                }
            }
        }
        return found
    }

    it('every var(--x) in src resolves to a property declared somewhere in src', () => {
        const declared = new Set([
            ...collect(CSS_DECL).keys(),
            ...collect(JS_DECL, [1, 2]).keys(),
        ])
        const undeclared = [...collect(USE)]
            .filter(([name]) => !declared.has(name))
            .map(([name, where]) => `${name} — used in ${[...new Set(where)].join(', ')}`)

        expect(undeclared).toEqual([])
    })

    it('the base :root in index.css declares every token the themes swap', () => {
        // applyTheme() only overrides; a token a theme sets but :root does not
        // declare would be undefined until the user's first theme switch.
        const root = readFileSync(join(SRC, 'index.css'), 'utf-8').split('}')[0]
        const rootTokens = new Set([...root.matchAll(CSS_DECL)].map((m) => m[1]))
        for (const slug of THEME_SLUGS) {
            for (const token of Object.keys(THEMES[slug].vars)) {
                expect([...rootTokens], `${slug} sets ${token}`).toContain(token)
            }
        }
    })

    it('each theme --accent-rgb is the RGB triplet of its own --accent', () => {
        // rgba(var(--accent-rgb), …) sites cannot use --accent (a hex), so the
        // triplet is duplicated per theme; this keeps the copy honest.
        for (const slug of THEME_SLUGS) {
            const { vars } = THEMES[slug]
            const [, r, g, b] = /^#(\w{2})(\w{2})(\w{2})$/.exec(vars['--accent'])
            expect(vars['--accent-rgb'], slug).toBe(
                [r, g, b].map((h) => parseInt(h, 16)).join(', '),
            )
        }
    })
})
