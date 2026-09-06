package com.booksync.ui.reader

/**
 * Rewrites a self-closing `<head/>` into an empty `<head></head>` pair — issue
 * #373.
 *
 * Readium 3.1.2's `ReadiumCss` injects the reader's stylesheet and JavaScript
 * by locating a literal `</head>` in the resource and splicing its tags in
 * front of it. `<head/>` is perfectly well-formed XHTML and there is no such
 * tag, so the injector throws
 *
 *     No </head> closing tag found in this resource
 *
 * while the WebView is streaming the response. The WebView sees the aborted
 * stream as `net::ERR_FAILED` and renders Chromium's error page, and because
 * the toolbar toggle *and* the page turns are both driven by the JavaScript
 * that never got injected, the reader is then stuck on that book with only the
 * system Back button. Calibre writes exactly this shape for the SVG cover title
 * page it generates, so a meaningful share of a Calibre-managed library trips
 * it.
 *
 * The fix is a Readium resource transformer wired into every publication the
 * reader opens (see `ReaderActivity.loadPublication`); this file is the pure,
 * testable half of it. The user's files are never modified — the rewrite
 * happens in memory, between the container and the injector.
 *
 * The contract is deliberately narrow: fix the self-closing head, and leave
 * every other byte alone. Anything looser would be a content rewriter running
 * over every page of every book.
 */

/**
 * Matches a self-closing `head` element, preserving the tag's own case and its
 * attributes.
 *
 *  * `(?=[\s/>])` after `head` is what keeps `<header/>` (and `<headerfoo/>`)
 *    out; a plain word boundary would not, since `head` and `header` share one.
 *  * The attribute alternation lets a quoted value hold `/` or `>` without
 *    ending the tag early — `<head profile="http://…/"/>` is real.
 *  * `\s*` on both sides of the slash covers `<head />` and a tag broken across
 *    lines.
 */
private val SELF_CLOSING_HEAD = Regex(
    """<(head)(?=[\s/>])((?:[^>"']|"[^"]*"|'[^']*')*?)\s*/\s*>""",
    RegexOption.IGNORE_CASE,
)

/**
 * Returns [html] with every self-closing `<head/>` replaced by an equivalent
 * `<head></head>` pair, and returns the *same instance* when there is nothing
 * to fix — which is the common case, and what makes "leaves everything else
 * byte-identical" true by construction rather than by inspection.
 *
 * The tag's case is preserved (`<HEAD/>` becomes `<HEAD></HEAD>`) because
 * Readium's search for the closing tag is case-insensitive, so there is no
 * reason to normalize the document's own spelling.
 */
fun normalizeEpubHead(html: String): String {
    if (!SELF_CLOSING_HEAD.containsMatchIn(html)) return html
    return SELF_CLOSING_HEAD.replace(html) { match ->
        val name = match.groupValues[1]
        val attributes = match.groupValues[2].trimEnd()
        "<$name$attributes></$name>"
    }
}

/**
 * The byte-level form used by the transformer.
 *
 * Decoding as ISO-8859-1 is not a guess about the document's encoding — it is
 * the one charset that round-trips *any* byte sequence unchanged, so a UTF-8
 * (or UTF-16, or mis-declared) resource comes back exactly as it went in. The
 * tag being matched is pure ASCII, which Latin-1 and UTF-8 agree on, so the
 * search still works on UTF-8 content.
 *
 * Returns the same array when nothing changed, so untouched resources cost one
 * decode and no allocation of a new payload.
 */
fun normalizeEpubHeadBytes(bytes: ByteArray): ByteArray {
    val text = String(bytes, Charsets.ISO_8859_1)
    val normalized = normalizeEpubHead(text)
    if (normalized === text) return bytes
    return normalized.toByteArray(Charsets.ISO_8859_1)
}

/**
 * Whether a container entry's file extension is one Readium will run the HTML
 * injector over. Everything else — images, CSS, fonts, the OPF, the NCX — is
 * passed through untransformed.
 */
fun isHtmlLikeExtension(extension: String?): Boolean =
    when (extension?.lowercase()) {
        "xhtml", "html", "htm" -> true
        else -> false
    }
