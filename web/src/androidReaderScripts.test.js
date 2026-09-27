import { describe, it, expect, beforeEach } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

/**
 * Issue #736: the two scripts the Android reader runs in WebViews had no CI
 * tests - their Kotlin twins were compared by hand. They are plain browser
 * scripts, so they run here in jsdom; jsdom lays nothing out, so the layout
 * values they read (scroll position, widths, element boxes) are stubbed.
 */
const ASSETS = join(__dirname, '..', '..', 'android', 'app', 'src', 'main', 'assets', 'tandem')

function load(name) {
    // eslint-disable-next-line no-new-func
    new Function(readFileSync(join(ASSETS, name), 'utf8')).call(window)
}

// ---------------------------------------------------------------------------
// live-probe.js: which screen page the reader shows (RTL fixed in #736)
// ---------------------------------------------------------------------------

describe('live-probe.js', () => {
    const W = 400

    function page({ scrollLeft, scrollWidth, dir = 'ltr', elements = {} }) {
        document.documentElement.setAttribute('dir', dir)
        document.documentElement.style.direction = dir
        document.body.innerHTML = Object.keys(elements).map(id => `<span id="${id}"></span>`).join('')
        for (const [id, left] of Object.entries(elements)) {
            document.getElementById(id).getBoundingClientRect = () => ({ left, right: left + 10, top: 0, bottom: 10, width: 10, height: 10 })
        }
        Object.defineProperty(document, 'scrollingElement', {
            configurable: true, value: { scrollLeft, scrollWidth },
        })
        Object.defineProperty(window, 'innerWidth', { configurable: true, value: W })
        return JSON.parse(window.tandemLiveProbe(Object.keys(elements)))
    }

    beforeEach(() => load('live-probe.js'))

    it('reads the page and total of a left-to-right resource', () => {
        const r = page({ scrollLeft: 2 * W, scrollWidth: 5 * W })
        expect(r).toEqual({ page: 3, total: 5, before: [] })
    })

    it('lists the markers at or before the page, left to right', () => {
        // On page 3 (scrollLeft 800): a marker on page 2 sits at -300 on screen,
        // one on page 3 at 50, one on page 4 at 450.
        const r = page({ scrollLeft: 2 * W, scrollWidth: 5 * W, elements: { p2: -300, p3: 50, p4: 450 } })
        expect(r.before).toEqual(['p2', 'p3'])
    })

    it('reads the page of a right-to-left resource, whose scrollLeft is negative', () => {
        const r = page({ scrollLeft: -2 * W, scrollWidth: 5 * W, dir: 'rtl' })
        expect(r).toEqual({ page: 3, total: 5, before: [] })
    })

    it('lists the markers at or before the page, right to left', () => {
        // On page 3 of an RTL resource: page 2 lies to the right of the screen,
        // page 4 to the left.
        const r = page({ scrollLeft: -2 * W, scrollWidth: 5 * W, dir: 'rtl', elements: { p2: 450, p3: 50, p4: -300 } })
        expect(r.before).toEqual(['p2', 'p3'])
    })

    it('treats the first page of an RTL resource as page 1, from its direction alone', () => {
        const r = page({ scrollLeft: 0, scrollWidth: 3 * W, dir: 'rtl', elements: { first: 100, second: -300 } })
        expect(r).toEqual({ page: 1, total: 3, before: ['first'] })
    })

    it('skips a marker that is not in the document', () => {
        document.body.innerHTML = ''
        Object.defineProperty(document, 'scrollingElement', { configurable: true, value: { scrollLeft: 0, scrollWidth: W } })
        expect(JSON.parse(window.tandemLiveProbe(['gone'])).before).toEqual([])
    })
})

// ---------------------------------------------------------------------------
// page-counter.js: the pure helpers, checked by hand until now
// ---------------------------------------------------------------------------

describe('page-counter.js helpers', () => {
    let pure
    beforeEach(() => {
        load('page-counter.js')
        pure = window.tandemPageCounterPure
    })

    it('hasStyles matches Readium: a link, a style attribute or a style element', () => {
        expect(pure.hasStyles('<html><head><link rel="stylesheet" href="a.css"></head></html>')).toBe(true)
        expect(pure.hasStyles('<p style="color:red">x</p>')).toBe(true)
        expect(pure.hasStyles('<style>p{}</style>')).toBe(true)
        expect(pure.hasStyles('<p>plain</p>')).toBe(false)
    })

    it('resolveLang falls back to the publication language only when html has none', () => {
        const none = { lang: null, xmlLang: null }
        expect(pure.resolveLang(true, none, none, 'fr')).toEqual({ html: 'fr', body: 'fr' })
        expect(pure.resolveLang(true, { lang: 'de', xmlLang: null }, none, 'fr')).toEqual({ html: 'de', body: null })
        expect(pure.resolveLang(true, none, { lang: 'es', xmlLang: null }, 'fr')).toEqual({ html: 'es', body: 'es' })
        expect(pure.resolveLang(false, none, none, null)).toEqual({ html: null, body: null })
    })

    it('resolveLang reads xml:lang first in XHTML and ignores it in HTML', () => {
        const both = { lang: 'en', xmlLang: 'en-GB' }
        const none = { lang: null, xmlLang: null }
        expect(pure.resolveLang(true, both, none, null).html).toBe('en-GB')
        expect(pure.resolveLang(false, both, none, null).html).toBe('en')
    })

    it('sanitize removes scripts and inline handlers and sandboxes frames', () => {
        const root = document.createElement('div')
        root.setAttribute('onclick', 'x()')
        root.innerHTML = '<script>bad()</script><p onmouseover="y()" class="k">t</p><iframe src="a.html"></iframe>'
        pure.sanitize(root)
        expect(root.querySelector('script')).toBeNull()
        expect(root.hasAttribute('onclick')).toBe(false)
        expect(root.querySelector('p').hasAttribute('onmouseover')).toBe(false)
        expect(root.querySelector('p').getAttribute('class')).toBe('k')
        expect(root.querySelector('iframe').getAttribute('sandbox')).toBe('')
    })
})
