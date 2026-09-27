import { RENDITION_OPTIONS, READER_THEME_RULES, fontSizeCss } from './readerRendition'

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
                await rendition.display(items[i].href)
                counts.push(Math.max(1, rendition.currentLocation()?.start?.displayed?.total || 1))
                const doc = rendition.getContents()[0]?.document
                chars.push((doc?.body?.textContent || '').replace(/\s+/g, ' ').trim().length)
            }
            onProgress?.(i + 1, items.length)
        }
        return { counts, chars }
    } finally {
        try { rendition?.destroy() } catch { /* already gone */ }
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
