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

// WCAG 2 relative luminance and contrast ratio, shared by the --on-accent
// (#694) and --accent-ink (#705) checks below.
function luminance(hex) {
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
        .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b
}
function contrast(a, b) {
    const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
    return (hi + 0.05) / (lo + 0.05)
}

// ---------------------------------------------------------------------------
// Issue #694: text painted on an accent fill used a hard-coded `white`, which
// only suits Blueprint's dark violet. The other four themes have light accents
// (green, amber, cyan, sky), where white measured 2.1–2.4:1 against the fill,
// far under WCAG AA's 4.5:1 for normal text. `--on-accent` is the ink for any
// text or icon sitting on `--accent` (or its hover shade), chosen per theme.
describe('--on-accent', () => {
    const SRC = resolve(dirname(fileURLToPath(import.meta.url)))

    it('every theme declares it, and it clears 4.5:1 on the accent and its hover shade', () => {
        for (const slug of THEME_SLUGS) {
            const { vars } = THEMES[slug]
            expect(vars['--on-accent'], `${slug} declares --on-accent`).toMatch(/^#[0-9a-f]{6}$/i)
            for (const fill of ['--accent', '--accent-hover']) {
                expect(contrast(vars['--on-accent'], vars[fill]), `${slug}: --on-accent on ${fill}`)
                    .toBeGreaterThanOrEqual(4.5)
            }
        }
    })

    function sourceFiles(dir) {
        return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
            const full = join(dir, entry.name)
            if (entry.isDirectory()) return sourceFiles(full)
            if (/\.test\.[jt]sx?$/.test(entry.name)) return []
            return /\.(css|jsx?)$/.test(entry.name) ? [full] : []
        })
    }

    // A background that is the accent, its hover shade, or a gradient starting
    // at the accent. `--accent-light`/`--accent-glow` are translucent tints over
    // the dark page, where white text is fine, so they are not accent fills.
    const ACCENT_FILL = /background(?:-color)?\s*:[^;]*var\(--accent(?:-hover)?\)/
    const WHITE_INK = /(?:^|[;{\s])color\s*:\s*(?:white|#fff(?:fff)?|rgba?\(\s*255\s*,\s*255\s*,\s*255)/i

    it('no CSS rule paints white text on an accent fill', () => {
        const offenders = []
        for (const file of sourceFiles(SRC).filter((f) => f.endsWith('.css'))) {
            const text = readFileSync(file, 'utf-8')
            for (const [, selector, body] of text.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
                if (ACCENT_FILL.test(body) && WHITE_INK.test(body)) {
                    offenders.push(`${file.slice(SRC.length + 1)}: ${selector.trim().split('\n').pop().trim()}`)
                }
            }
        }
        expect(offenders).toEqual([])
    })

    it('no inline style object paints white text on an accent fill', () => {
        const INLINE_BG = /background\s*:\s*['"`][^'"`]*var\(--accent(?:-hover)?\)/
        const INLINE_WHITE = /\bcolor\s*:\s*['"](?:white|#fff(?:fff)?)['"]/i
        const offenders = []
        for (const file of sourceFiles(SRC).filter((f) => /\.jsx?$/.test(f))) {
            const text = readFileSync(file, 'utf-8')
            for (const [, body] of text.matchAll(/style=\{\{([\s\S]*?)\}\}/g)) {
                if (INLINE_BG.test(body) && INLINE_WHITE.test(body)) {
                    offenders.push(`${file.slice(SRC.length + 1)}: ${body.trim().slice(0, 60)}`)
                }
            }
        }
        expect(offenders).toEqual([])
    })

    it('the placeholder cover initial, on an accent gradient from a separate rule, uses it', () => {
        // Its colour and its background live in different rules, so the scan
        // above cannot pair them; this one is pinned by name.
        const css = readFileSync(join(SRC, 'index.css'), 'utf-8')
        const rule = /\.book-detail-cover-initial\s*\{([^}]*)\}/.exec(css)[1]
        expect(rule).toMatch(/color\s*:\s*var\(--on-accent\)/)
    })
})

// ---------------------------------------------------------------------------
// Issue #705: the mirror of #694. Blueprint's accent (#7c3aed) used as *ink*
// on its own dark surfaces measured 2.5-3.3:1 — under 4.5:1 for text and even
// under the 3:1 WCAG asks of focus rings, active borders and underlines. The
// other four themes' accents already clear 5.6:1. `--accent-ink` is the colour
// for anything drawn *in* the accent on a dark surface: text, borders, outlines
// and focus rings. Accent *fills* keep `--accent` (their labels use
// `--on-accent`, #694).
describe('--accent-ink', () => {
    const SRC = resolve(dirname(fileURLToPath(import.meta.url)))

    function mix(fgHex, bgHex, alpha) {
        const ch = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16))
        const [f, b] = [ch(fgHex), ch(bgHex)]
        return '#' + f.map((v, i) => Math.round(v * alpha + b[i] * (1 - alpha))
            .toString(16).padStart(2, '0')).join('')
    }

    it('every theme declares it, and it clears 4.5:1 on every surface it is drawn on', () => {
        for (const slug of THEME_SLUGS) {
            const { vars } = THEMES[slug]
            const ink = vars['--accent-ink']
            expect(ink, `${slug} declares --accent-ink`).toMatch(/^#[0-9a-f]{6}$/i)
            const surfaces = {
                '--bg-primary': vars['--bg-primary'],
                '--bg-secondary': vars['--bg-secondary'],
                '--bg-card': vars['--bg-card'],
                '--bg-card-hover': vars['--bg-card-hover'],
                '--bg-input': vars['--bg-input'],
                // An active pill: the 15 % --accent-light tint over a card.
                'accent tint on --bg-card': mix(vars['--accent'], vars['--bg-card'], 0.15),
            }
            for (const [name, bg] of Object.entries(surfaces)) {
                expect(contrast(ink, bg), `${slug}: --accent-ink on ${name}`).toBeGreaterThanOrEqual(4.5)
            }
        }
    })

    function sourceFiles(dir) {
        return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
            const full = join(dir, entry.name)
            if (entry.isDirectory()) return sourceFiles(full)
            if (/\.test\.[jt]sx?$/.test(entry.name)) return []
            return /\.(css|jsx?)$/.test(entry.name) ? [full] : []
        })
    }

    const ACCENT_FILL = /background(?:-color)?\s*:[^;]*var\(--accent(?:-hover)?\)/
    const ACCENT_TEXT = /(?:^|[;{\s])color\s*:\s*var\(--accent(?:-hover)?\)/
    const ACCENT_LINE = /(?:^|[;{\s])(?:border[\w-]*|outline[\w-]*|box-shadow)\s*:[^;]*var\(--accent(?:-hover)?\)/

    it('no CSS rule draws text, a border, an outline or a focus ring in the raw accent', () => {
        const offenders = []
        for (const file of sourceFiles(SRC).filter((f) => f.endsWith('.css'))) {
            const text = readFileSync(file, 'utf-8')
            for (const [, selector, body] of text.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
                const where = `${file.slice(SRC.length + 1)}: ${selector.trim().split('\n').pop().trim()}`
                if (ACCENT_TEXT.test(body)) offenders.push(`${where} (text)`)
                // A border on an accent-filled element is the fill's own edge.
                if (ACCENT_LINE.test(body) && !ACCENT_FILL.test(body)) offenders.push(`${where} (line)`)
            }
        }
        expect(offenders).toEqual([])
    })

    it('no inline style colours text or a border with the raw accent', () => {
        const INLINE = /\b(?:color|border\w*|outline\w*)\s*:\s*['"`][^'"`]*var\(--accent(?:-hover)?\)/
        const offenders = []
        for (const file of sourceFiles(SRC).filter((f) => /\.jsx?$/.test(f))) {
            const text = readFileSync(file, 'utf-8')
            for (const [, body] of text.matchAll(/style=\{\{([\s\S]*?)\}\}/g)) {
                if (INLINE.test(body)) offenders.push(`${file.slice(SRC.length + 1)}: ${body.trim().slice(0, 60)}`)
            }
        }
        expect(offenders).toEqual([])
    })
})
