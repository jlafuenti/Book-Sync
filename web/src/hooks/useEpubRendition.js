import { useEffect, useRef, useState } from 'react'
import ePub from 'epubjs'
import { fetchEbookBlob } from '../api'

/**
 * The epub.js lifecycle for one book (issue #278): blob fetch, `ePub()`,
 * `renderTo`, the structural theme, the per-chapter <style> injection, the
 * TOC, the locations pass, and teardown.
 *
 *     const { bookRef, renditionRef, toc, loading, error } = useEpubRendition(
 *         viewerRef, ebookId, { fontSizeRef, paletteRef, onOpened, onTeardown },
 *     )
 *
 * `onOpened({ book, rendition, nav, isDestroyed })` is awaited in the SAME
 * async continuation, after the TOC has loaded and before the spinner clears.
 * That is where `EbookReader` runs the restore ladder and registers its
 * `relocated` handler — and the ordering is load-bearing: the handler must be
 * attached after the restore has landed (so its save path is gated on a
 * real position) and before the locations pass re-reports the location.
 * `isDestroyed()` tells a long-running callback that the book it is working
 * on has been torn down. Both callbacks are read from refs, so an inline
 * arrow does not reopen the book.
 *
 * `onTeardown()` runs on unmount / `ebookId` change BEFORE `book.destroy()`.
 * The reader flushes its pending position write there, and the payload's
 * text preview is extracted from the epub iframe DOM that destroy() tears
 * down (issue #158).
 *
 * The book and rendition are returned as refs, not state: they are set the
 * moment they exist, and every callback in the reader (text extraction,
 * keyboard navigation, the theme effects) reads them synchronously.
 *
 * `fontSizeRef` / `paletteRef` are read inside the content hook at render
 * time, so a preference changed after registration still styles the next
 * chapter (issue #57).
 */
export default function useEpubRendition(viewerRef, ebookId, {
    fontSizeRef, paletteRef, onOpened, onTeardown,
}) {
    const bookRef = useRef(null)
    const renditionRef = useRef(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)
    const [toc, setToc] = useState([])

    const onOpenedRef = useRef(onOpened)
    onOpenedRef.current = onOpened
    const onTeardownRef = useRef(onTeardown)
    onTeardownRef.current = onTeardown

    useEffect(() => {
        let destroyed = false
        const isDestroyed = () => destroyed

        async function loadBook() {
            try {
                const arrayBuffer = await fetchEbookBlob(ebookId)
                if (destroyed) return

                const book = ePub(arrayBuffer)
                bookRef.current = book

                const rendition = book.renderTo(viewerRef.current, {
                    width: '100%',
                    height: '100%',
                    flow: 'paginated',
                    spread: 'none',
                })
                renditionRef.current = rendition

                // Structural styles only — colors come from the injected
                // #tandem-reader-theme <style> below, so the palette can
                // change live with the reader-theme picker (issue #57).
                rendition.themes.default({
                    'body': {
                        'font-family': 'Georgia, "Times New Roman", serif !important',
                        'padding': '0 48px !important',
                        'max-width': '100% !important',
                        'box-sizing': 'border-box !important',
                    },
                    'img': { 'max-width': '100% !important' },
                    '*': { 'max-width': '100% !important', 'box-sizing': 'border-box !important' },
                })

                // Inject font size + palette via <style> tags (avoids blob URL
                // MIME rejection; CSS vars don't cross into the iframe). Reads
                // the refs so a pref change after registration still applies.
                rendition.hooks.content.register(contents => {
                    if (contents.document) {
                        const style = contents.document.createElement('style')
                        style.id = 'tandem-font-size'
                        style.textContent = `html { font-size: ${fontSizeRef.current}% !important; }`
                        contents.document.head.appendChild(style)

                        const themeStyle = contents.document.createElement('style')
                        themeStyle.id = 'tandem-reader-theme'
                        themeStyle.textContent = paletteCss(paletteRef.current)
                        contents.document.head.appendChild(themeStyle)
                    }
                })

                // Load TOC
                const nav = await book.loaded.navigation
                if (!destroyed) {
                    setToc(nav.toc || [])
                }

                // Restore + relocated handler live with the caller.
                await onOpenedRef.current?.({ book, rendition, nav, isDestroyed })
                if (destroyed) return

                setLoading(false)

                // Generate locations for accurate percentages
                book.ready.then(() => {
                    return book.locations.generate(1024)
                }).then(() => {
                    if (!destroyed && renditionRef.current) {
                        renditionRef.current.reportLocation()
                    }
                })
            } catch (err) {
                if (!destroyed) {
                    setError(err.message || 'Failed to load ebook')
                    setLoading(false)
                }
            }
        }

        loadBook()

        return () => {
            destroyed = true
            onTeardownRef.current?.()
            if (bookRef.current) {
                try { bookRef.current.destroy() } catch (e) {}
            }
        }
    }, [ebookId]) // eslint-disable-line react-hooks/exhaustive-deps

    return { bookRef, renditionRef, toc, loading, error }
}

// The palette must be injected as a <style> into each chapter document — CSS
// variables set on the parent document never cascade into the epub iframe.
export function paletteCss(palette) {
    return [
        `html, body { background: ${palette.background} !important; color: ${palette.text} !important; }`,
        `h1, h2, h3, h4, h5, h6, p, span, div, li, td, th { color: ${palette.text} !important; }`,
        `a { color: ${palette.link} !important; }`,
    ].join('\n')
}
