package com.booksync.ui.reader

import com.booksync.data.local.entity.SyncPointEntity

/**
 * The text the page is searched for a read-along sentence, with a little of
 * its surroundings (issue #793). Mirrors Readium's `Locator.Text`: [highlight]
 * is the sentence, [before] and [after] the text next to it.
 */
data class SentenceQuote(val before: String?, val highlight: String, val after: String?)

/**
 * Finds the quote context for each sync point of one following session.
 *
 * Readium resolves a text quote by approximate search over the chapter and,
 * given only the sentence, settles a tie on the first match. A line that is
 * repeated in a chapter (a speaker tag, a refrain, "Chapter 1" in a running
 * head) was therefore highlighted, and jumped to, at its first occurrence
 * wherever the audio actually was. The remedy is the same one a human uses:
 * say what comes before and after. Readium weights those prefix/suffix
 * similarities into the match score, so two copies of one line, each with
 * different neighbours, no longer tie.
 *
 * Every EPUB sentence has a sync point (matched or interpolated), so the
 * neighbours of sentence `i` are the points at `i - 1` and `i + 1` in the
 * same chapter. The index is built once when following starts, not per tick.
 */
class SentenceQuoteIndex(points: List<SyncPointEntity>) {
    private val byPosition: Map<Pair<Int, Int>, SyncPointEntity> =
        points.associateBy { it.epubChapter to it.epubSentenceIndex }

    /** Null when the point has nothing to quote. */
    fun quoteFor(point: SyncPointEntity): SentenceQuote? {
        val lines = linesOf(point.epubTextPreview)
        val highlight = lines.firstOrNull() ?: return null
        val previous = byPosition[point.epubChapter to point.epubSentenceIndex - 1]
        val next = byPosition[point.epubChapter to point.epubSentenceIndex + 1]
        return SentenceQuote(
            before = previous?.let(::tailOf),
            highlight = highlight,
            after = when {
                // The quote stops at a block boundary: what follows it on the
                // page is the preview's own next line, not the next sentence.
                lines.size > 1 -> lines[1].take(CONTEXT_CHARS)
                // A capped preview is cut mid-sentence: what follows the quote
                // is the rest of this sentence, which we do not have.
                isCapped(point.epubTextPreview) -> null
                else -> next?.let(::headOf)
            },
        )
    }

    private fun headOf(neighbour: SyncPointEntity): String? =
        linesOf(neighbour.epubTextPreview).firstOrNull()?.take(CONTEXT_CHARS)

    /**
     * The last [CONTEXT_CHARS] characters of the neighbour, but only when its
     * preview is whole: a capped one ends mid-sentence, so its tail is not
     * what precedes the current sentence on the page. Taken from the last
     * line, since that is the text that sits against the quote.
     */
    private fun tailOf(neighbour: SyncPointEntity): String? {
        if (isCapped(neighbour.epubTextPreview)) return null
        return linesOf(neighbour.epubTextPreview).lastOrNull()?.takeLast(CONTEXT_CHARS)
    }

    /**
     * Whether [preview] may be a sentence cut at the old server cap. Maps
     * aligned since issue #763 store the whole sentence, so a longer preview
     * is whole; one of exactly [PREVIEW_CAP] characters is treated as cut, at
     * the price of a genuine 200-character sentence losing its context.
     */
    private fun isCapped(preview: String?): Boolean = preview?.length == PREVIEW_CAP

    private fun linesOf(preview: String?): List<String> =
        preview?.lineSequence()?.map { it.trim() }?.filter { it.isNotEmpty() }?.toList().orEmpty()

    companion object {
        /** How much of a neighbouring sentence to use as context. */
        const val CONTEXT_CHARS = 40

        /**
         * The server cut `epubTextPreview` to this many characters until
         * issue #763; maps cached from before their next realign still are.
         */
        const val PREVIEW_CAP = 200
    }
}

/**
 * JavaScript for `ReaderActivity.isSentenceVisible`: finds the sentence in the
 * current chapter's document and answers 'visible', 'hidden' (found, but on
 * another page) or 'missing' (not in this chapter).
 *
 * Like Readium's locator search, it picks among repeated occurrences by the
 * text around them (issue #793): the one whose preceding page text shares the
 * longest suffix with [before] plus whose following page text shares the
 * longest prefix with [after]; a tie keeps the first. Comparison is on text
 * with a space before `. , ; : ! ?` removed on both sides, because the
 * server's sentence tokenizer can leave `Name : text` where the page says
 * `Name: text`, and an exact match would then call a sentence on screen
 * missing and force a needless page jump.
 */
fun sentenceVisibilityScript(quote: String, before: String?, after: String?): String = """
(function(quote, before, after) {
    function collapse(s) { return s.replace(/\s+/g, ' ').trim(); }
    // Text with the space before punctuation dropped; map[i] is the index in
    // the input of squashed character i.
    function squash(s) {
        var out = [], map = [];
        for (var i = 0; i < s.length; i++) {
            if (s.charAt(i) === ' ' && i + 1 < s.length && '.,;:!?'.indexOf(s.charAt(i + 1)) >= 0) continue;
            out.push(s.charAt(i));
            map.push(i);
        }
        return { text: out.join(''), map: map };
    }
    var full = squash(collapse(quote)).text;
    var want = full.substring(0, 60);
    if (!want) return 'missing';
    var ctxBefore = squash(collapse(before)).text;
    var ctxAfter = squash(collapse(after)).text;
    var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
    var chars = [], owners = [], offsets = [], lastSpace = true, node;
    while ((node = walker.nextNode())) {
        var v = node.nodeValue;
        for (var i = 0; i < v.length; i++) {
            var ch = v.charAt(i);
            if (/\s/.test(ch)) {
                if (lastSpace) continue;
                ch = ' ';
                lastSpace = true;
            } else {
                lastSpace = false;
            }
            chars.push(ch);
            owners.push(node);
            offsets.push(i);
        }
    }
    var page = squash(chars.join(''));
    function commonSuffix(a, b) {
        var n = 0;
        while (n < a.length && n < b.length && a.charAt(a.length - 1 - n) === b.charAt(b.length - 1 - n)) n++;
        return n;
    }
    function commonPrefix(a, b) {
        var n = 0;
        while (n < a.length && n < b.length && a.charAt(n) === b.charAt(n)) n++;
        return n;
    }
    var best = -1, bestScore = -1, from = 0, at;
    while ((at = page.text.indexOf(want, from)) >= 0) {
        var score = 0;
        if (ctxBefore) score += commonSuffix(ctxBefore, page.text.substring(Math.max(0, at - ctxBefore.length - 1), at).replace(/ +$/, ''));
        if (ctxAfter) score += commonPrefix(ctxAfter, page.text.substring(at + full.length).replace(/^ +/, ''));
        if (score > bestScore) { best = at; bestScore = score; }
        from = at + 1;
    }
    if (best < 0) return 'missing';
    var first = page.map[best];
    var last = page.map[best + want.length - 1];
    var range = document.createRange();
    range.setStart(owners[first], offsets[first]);
    range.setEnd(owners[last], offsets[last] + 1);
    var rects = range.getClientRects();
    var vpW = window.innerWidth;
    for (var r = 0; r < rects.length; r++) {
        // The current column's fragments sit in [0, vpW).
        if (rects[r].width > 0 && rects[r].left >= -1 && rects[r].left < vpW) return 'visible';
    }
    return 'hidden';
})(${jsString(quote)}, ${jsString(before)}, ${jsString(after)})
""".trimIndent()

/** A JavaScript string literal for [s]; null becomes the empty string. */
internal fun jsString(s: String?): String {
    val sb = StringBuilder("\"")
    for (c in s.orEmpty()) {
        when (c) {
            '\\' -> sb.append("\\\\")
            '"' -> sb.append("\\\"")
            '\n' -> sb.append("\\n")
            '\r' -> sb.append("\\r")
            ' ' -> sb.append("\\u2028")
            ' ' -> sb.append("\\u2029")
            else -> if (c < ' ') sb.append("\\u%04x".format(c.code)) else sb.append(c)
        }
    }
    return sb.append('"').toString()
}
