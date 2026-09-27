import { describe, it, expect, vi } from 'vitest'
import { countSectionPages, spineSignature } from './pageCounter'
import { fontSizeCss } from './readerRendition'

// Fake epub.js rendition: display(href) records which section is showing and
// reports its page total via currentLocation(), the same shape epub.js uses.
// jsdom cannot lay out real pages, so this stands in for real pagination.
//
// Real epub.js resolves rendition.display() before its hooks.content ->
// 'rendered' chain finishes (rendition.js `afterDisplayed`), so this fake
// models that: display() applies the registered content hook and emits
// 'rendered' (with a {href} "section") itself, and callers can delay or
// suppress that emit to test the ordering:
//   - `renderedDelayMs` fires it on a real timer, after display() has
//     already resolved (the case that matters: reading the count too early
//     would see pre-hook state).
//   - `neverEmitRendered` never fires it at all, exercising the timeout.
// A totalsByHref entry can be `{ before, after }` to make the reported page
// total depend on whether the content hook (and so 'rendered') has actually
// fired for that section yet, rather than a single fixed number.
function createFakeRendition({ totalsByHref, textByHref, throwOnHref, renderedDelayMs, neverEmitRendered } = {}) {
    let contentHook = null
    const listeners = {}
    return {
        themes: { default: () => {} },
        hooks: { content: { register: cb => { contentHook = cb } } },
        destroyed: false,
        _currentHref: null,
        _hookApplied: false,
        injectedStyles: [],
        on(event, fn) { (listeners[event] ||= []).push(fn) },
        off(event, fn) { listeners[event] = (listeners[event] || []).filter(f => f !== fn) },
        emit(event, ...args) { [...(listeners[event] || [])].forEach(fn => fn(...args)) },
        listenerCount(event) { return (listeners[event] || []).length },
        async display(href) {
            if (throwOnHref && href === throwOnHref) throw new Error('display failed')
            this._currentHref = href
            this._hookApplied = false
            const applyHookAndEmitRendered = () => {
                if (contentHook) {
                    const el = { textContent: null }
                    contentHook({ document: { createElement: () => el, head: { appendChild: () => this.injectedStyles.push(el.textContent) } } })
                    this._hookApplied = true
                }
                this.emit('rendered', { href })
            }
            if (neverEmitRendered) return
            if (renderedDelayMs != null) { setTimeout(applyHookAndEmitRendered, renderedDelayMs); return }
            applyHookAndEmitRendered()
        },
        currentLocation() {
            const spec = totalsByHref[this._currentHref]
            const total = spec && typeof spec === 'object' ? (this._hookApplied ? spec.after : spec.before) : spec
            return { start: { displayed: { total } } }
        },
        getContents() {
            return [{ document: { body: { textContent: textByHref[this._currentHref] || '' } } }]
        },
        destroy() { this.destroyed = true },
    }
}

// The real Book.destroy() also destroys book.rendition, so the fake cascades
// the same way rather than pageCounter.js destroying the rendition itself.
function createFakeBook({ items, rendition }) {
    return {
        ready: Promise.resolve(),
        spine: { spineItems: items },
        renderedHost: null,
        destroyed: false,
        renderTo(host) {
            this.renderedHost = host
            return rendition
        },
        destroy() {
            this.destroyed = true
            rendition?.destroy()
        },
    }
}

const THREE_ITEMS = [
    { href: 'ch1.xhtml', linear: true },
    { href: 'ch2.xhtml', linear: 'no' },
    { href: 'ch3.xhtml', linear: true },
]

describe('countSectionPages (issue #730)', () => {
    it('counts pages and chars per section, skipping non-linear sections', async () => {
        const rendition = createFakeRendition({
            totalsByHref: { 'ch1.xhtml': 3, 'ch3.xhtml': 5 },
            textByHref: { 'ch1.xhtml': 'Hello world', 'ch3.xhtml': 'A somewhat longer chapter of text' },
        })
        const book = createFakeBook({ items: THREE_ITEMS, rendition })
        const progressCalls = []
        const result = await countSectionPages(() => book, {
            width: 800, height: 600, fontSize: 100,
            onProgress: (done, total) => progressCalls.push([done, total]),
        })
        expect(result).toEqual({
            counts: [3, 0, 5],
            chars: ['Hello world'.length, 0, 'A somewhat longer chapter of text'.length],
        })
        expect(progressCalls).toEqual([[1, 3], [2, 3], [3, 3]])
    })

    it('treats linear: false the same as linear: "no"', async () => {
        const items = [
            { href: 'ch1.xhtml', linear: true },
            { href: 'ch2.xhtml', linear: false },
        ]
        const rendition = createFakeRendition({
            totalsByHref: { 'ch1.xhtml': 2 },
            textByHref: { 'ch1.xhtml': 'abc' },
        })
        const book = createFakeBook({ items, rendition })
        const result = await countSectionPages(() => book, { width: 800, height: 600, fontSize: 100 })
        expect(result).toEqual({ counts: [2, 0], chars: [3, 0] })
    })

    it('collapses whitespace before measuring chars', async () => {
        const items = [{ href: 'ch1.xhtml', linear: true }]
        const rendition = createFakeRendition({
            totalsByHref: { 'ch1.xhtml': 1 },
            textByHref: { 'ch1.xhtml': '  Hello   \n\n world  ' },
        })
        const book = createFakeBook({ items, rendition })
        const result = await countSectionPages(() => book, { width: 800, height: 600, fontSize: 100 })
        expect(result.chars).toEqual(['Hello world'.length])
    })

    it('injects the reader font-size CSS into every displayed section', async () => {
        const rendition = createFakeRendition({
            totalsByHref: { 'ch1.xhtml': 2, 'ch3.xhtml': 4 },
            textByHref: { 'ch1.xhtml': 'a', 'ch3.xhtml': 'b' },
        })
        const book = createFakeBook({ items: THREE_ITEMS, rendition })
        await countSectionPages(() => book, { width: 800, height: 600, fontSize: 130 })
        // Fired once per displayed (linear) section, not for the skipped one.
        expect(rendition.injectedStyles).toEqual([fontSizeCss(130), fontSizeCss(130)])
    })

    it('waits for the "rendered" event before reading the count, even though display() resolves first', async () => {
        // epub.js's display() can resolve before its hooks.content -> 'rendered'
        // chain finishes (rendition.js `afterDisplayed`); the content hook is
        // what applies the font-size CSS that affects layout. `before` models
        // the (wrong) count a premature read would see; `after` is correct.
        const rendition = createFakeRendition({
            totalsByHref: { 'ch1.xhtml': { before: 1, after: 7 } },
            textByHref: { 'ch1.xhtml': 'chapter text' },
            renderedDelayMs: 5,
        })
        const book = createFakeBook({ items: [{ href: 'ch1.xhtml', linear: true }], rendition })
        const result = await countSectionPages(() => book, { width: 800, height: 600, fontSize: 100 })
        expect(result.counts).toEqual([7])
    })

    it('reads the count anyway once the 3000 ms bound elapses, if "rendered" is never observed', async () => {
        vi.useFakeTimers()
        try {
            const rendition = createFakeRendition({
                totalsByHref: { 'ch1.xhtml': 4 },
                textByHref: { 'ch1.xhtml': 'abc' },
                neverEmitRendered: true,
            })
            const book = createFakeBook({ items: [{ href: 'ch1.xhtml', linear: true }], rendition })
            const resultPromise = countSectionPages(() => book, { width: 800, height: 600, fontSize: 100 })
            await vi.advanceTimersByTimeAsync(3000)
            const result = await resultPromise
            expect(result).toEqual({ counts: [4], chars: [3] })
        } finally {
            vi.useRealTimers()
        }
    })

    it('appends a hidden host element sized to the given viewport, then removes it', async () => {
        const rendition = createFakeRendition({ totalsByHref: { 'ch1.xhtml': 1 }, textByHref: { 'ch1.xhtml': 'x' } })
        const book = createFakeBook({ items: [{ href: 'ch1.xhtml', linear: true }], rendition })
        await countSectionPages(() => book, { width: 480, height: 640, fontSize: 100 })
        expect(book.renderedHost).not.toBeNull()
        expect(book.renderedHost.style.width).toBe('480px')
        expect(book.renderedHost.style.height).toBe('640px')
        expect(book.renderedHost.getAttribute('aria-hidden')).toBe('true')
        expect(book.renderedHost.inert).toBe(true)
        expect(document.body.contains(book.renderedHost)).toBe(false)
    })

    it('destroys the rendition and book and removes the host even when display throws', async () => {
        const rendition = createFakeRendition({
            totalsByHref: {},
            textByHref: {},
            throwOnHref: 'ch1.xhtml',
        })
        const book = createFakeBook({ items: THREE_ITEMS, rendition })
        await expect(countSectionPages(() => book, { width: 800, height: 600, fontSize: 100 })).rejects.toThrow('display failed')
        expect(rendition.destroyed).toBe(true)
        expect(book.destroyed).toBe(true)
        expect(document.body.contains(book.renderedHost)).toBe(false)
    })

    it('does not leak a "rendered" listener or a pending timer when display() throws', async () => {
        vi.useFakeTimers()
        try {
            const rendition = createFakeRendition({
                totalsByHref: {},
                textByHref: {},
                throwOnHref: 'ch1.xhtml',
            })
            const book = createFakeBook({ items: [{ href: 'ch1.xhtml', linear: true }], rendition })
            await expect(countSectionPages(() => book, { width: 800, height: 600, fontSize: 100 })).rejects.toThrow('display failed')
            expect(rendition.listenerCount('rendered')).toBe(0)
            expect(vi.getTimerCount()).toBe(0)
        } finally {
            vi.useRealTimers()
        }
    })

    it('rejects with an AbortError once the signal is aborted, before displaying the next section', async () => {
        const rendition = createFakeRendition({
            totalsByHref: { 'ch1.xhtml': 3, 'ch3.xhtml': 5 },
            textByHref: { 'ch1.xhtml': 'Hello world', 'ch3.xhtml': 'more text here' },
        })
        const book = createFakeBook({ items: THREE_ITEMS, rendition })
        const controller = new AbortController()
        const progressCalls = []
        const onProgress = (done, total) => {
            progressCalls.push([done, total])
            if (done === 1) controller.abort()
        }
        await expect(countSectionPages(() => book, {
            width: 800, height: 600, fontSize: 100, signal: controller.signal, onProgress,
        })).rejects.toMatchObject({ name: 'AbortError' })
        // Only the first (linear) section was displayed before the abort was observed.
        expect(progressCalls).toEqual([[1, 3]])
        expect(rendition.destroyed).toBe(true)
        expect(document.body.contains(book.renderedHost)).toBe(false)
    })
})

describe('spineSignature (issue #730)', () => {
    it('is stable for the same spine hrefs', () => {
        const book = { spine: { spineItems: THREE_ITEMS } }
        expect(spineSignature(book)).toBe(spineSignature(book))
    })

    it('changes when the spine hrefs change', () => {
        const bookA = { spine: { spineItems: THREE_ITEMS } }
        const bookB = { spine: { spineItems: [...THREE_ITEMS, { href: 'ch4.xhtml', linear: true }] } }
        expect(spineSignature(bookA)).not.toBe(spineSignature(bookB))
    })

    it('includes the item count so a same-hash truncation still differs', () => {
        const book = { spine: { spineItems: THREE_ITEMS } }
        expect(spineSignature(book)).toMatch(/^3:/)
    })
})
