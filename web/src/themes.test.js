import { describe, it, expect } from 'vitest'
import { THEMES, THEME_SLUGS, DEFAULT_THEME, READER_MODES, getReaderPalette } from './themes'

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
