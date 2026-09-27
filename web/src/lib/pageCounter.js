import { RENDITION_OPTIONS, READER_THEME_RULES, fontSizeCss } from './readerRendition'

const RENDERED_TIMEOUT_MS = 3000

// rendition.display() can resolve before epub.js's hooks.content -> 'rendered'
// chain finishes (epubjs/src/rendition.js `afterDisplayed`): hooks.render then
// hooks.content run against the section's view, and only once hooks.content
// resolves does the rendition emit `EVENTS.RENDITION.RENDERED` ('rendered')
// with (section, view). hooks.content is where our fontSizeCss style tag is
// appended, so reading currentLocation() right after display() resolves can
// see the section before that style landed. Registering the listener before
// calling display() (rather than after) means a 'rendered' that fires very
// quickly is never missed. Bounded at 3000 ms in case a section never emits
// it — the count is read anyway rather than hanging the whole scan.
function waitForRendered(rendition, href) {
    return new Promise(resolve => {
        let settled = false
        const onRendered = section => {
            if (settled || (section && section.href !== href)) return
            settled = true
            clearTimeout(timer)
            rendition.off('rendered', onRendered)
            resolve()
        }
        const timer = setTimeout(() => {
            if (settled) return
            settled = true
            rendition.off('rendered', onRendered)
            resolve()
        }, RENDERED_TIMEOUT_MS)
        rendition.on('rendered', onRendered)
    })
}

/**
 * Pages per spine section at the reader's live settings (issue #730), counted
 * off-screen in a rendition on its own Book so the reader's Book and rendition
 * are untouched. The spike measured ~65 ms a section on desktop Chrome and
 * matched the visible reader on every sampled section.
 */
export async function countSectionPages(openBook, { width, height, fontSize, signal, onProgress } = {}) {
    const book = openBook()
    const host = document.createElement('div')
    host.setAttribute('aria-hidden', 'true')
    Object.assign(host.style, { position: 'fixed', left: '-100000px', top: '0', width: `${width}px`, height: `${height}px`, overflow: 'hidden' })
    document.body.appendChild(host)
    let rendition = null
    try {
        await book.ready
        rendition = book.renderTo(host, RENDITION_OPTIONS)
        rendition.themes.default(READER_THEME_RULES)
        rendition.hooks.content.register(contents => {
            const s = contents.document.createElement('style')
            s.textContent = fontSizeCss(fontSize)
            contents.document.head.appendChild(s)
        })
        const items = book.spine.spineItems
        const counts = []
        const chars = []
        for (let i = 0; i < items.length; i++) {
            if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')
            if (items[i].linear === 'no' || items[i].linear === false) {
                counts.push(0); chars.push(0)
            } else {
                const rendered = waitForRendered(rendition, items[i].href)
                await rendition.display(items[i].href)
                await rendered
                counts.push(Math.max(1, rendition.currentLocation()?.start?.displayed?.total || 1))
                const doc = rendition.getContents()[0]?.document
                chars.push((doc?.body?.textContent || '').replace(/\s+/g, ' ').trim().length)
            }
            onProgress?.(i + 1, items.length)
        }
        return { counts, chars }
    } finally {
        // Book.destroy() already destroys book.rendition, so this is the only
        // rendition teardown call — no separate rendition?.destroy() alongside it.
        try { book.destroy() } catch { /* already gone */ }
        host.remove()
    }
}

export function spineSignature(book) {
    const s = book.spine.spineItems.map(i => i.href).join('|')
    let h = 5381
    for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0
    return `${book.spine.spineItems.length}:${(h >>> 0).toString(36)}`
}
