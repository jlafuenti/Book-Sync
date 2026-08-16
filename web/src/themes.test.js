import { describe, it, expect } from 'vitest'
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
