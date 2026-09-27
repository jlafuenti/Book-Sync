import { describe, it, expect } from 'vitest'
import { RENDITION_OPTIONS, READER_THEME_RULES, fontSizeCss } from './readerRendition'

describe('readerRendition', () => {
    it('fontSizeCss renders the font-size override for a given percentage', () => {
        expect(fontSizeCss(120)).toBe('html { font-size: 120% !important; }')
    })

    it('READER_THEME_RULES pins the body padding that sets the page width', () => {
        expect(READER_THEME_RULES.body.padding).toBe('0 48px !important')
    })

    it('RENDITION_OPTIONS matches the paginated, no-spread layout the reader and counter share', () => {
        expect(RENDITION_OPTIONS).toEqual({
            width: '100%',
            height: '100%',
            flow: 'paginated',
            spread: 'none',
        })
    })
})
