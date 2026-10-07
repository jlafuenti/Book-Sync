package com.booksync.ui.reader

import com.booksync.sync.WordTokens

/**
 * The read-along word mark (issue #836): the word being read, inside the
 * sentence that Readium's decoration already marks.
 *
 * It is drawn with the CSS Custom Highlight API rather than a Readium
 * decoration. A decoration is laid out once, from client rects, and re-applying
 * one every 150 ms would redo that work (and its relayout) for every word; a
 * highlight is a named list of DOM ranges the engine paints itself, so moving
 * the mark is one `CSS.highlights.set`. The ranges are built once per sentence
 * ([wordMarkLocateScript]); each word change then only picks one
 * ([wordMarkSetScript]).
 *
 * Everything here falls back to "sentence mark only": no word timing for the
 * sentence, a token count that does not match the timing, a sentence the page
 * search cannot find, or a WebView without `CSS.highlights`.
 */

/** The name the highlight is registered under, and the `::highlight()` rule's. */
internal const val WORD_HIGHLIGHT_NAME = "tandem-word"

/** Id of the `<style>` element the locate script appends to the page. */
internal const val WORD_STYLE_ID = "tandem-word-style"

/**
 * What to hand [wordMarkLocateScript] for one sentence: the text to search the
 * page for ([quote]) and the offsets of each whitespace token inside it
 * ([tokenRanges], in the whitespace-collapsed text, inclusive ends).
 */
internal class WordMarkPlan(val quote: SentenceQuote, val tokenRanges: List<IntRange>)

/**
 * Builds the plan from a sync point's [preview] and the [quote] the sentence
 * mark already uses. The search needs the whole sentence, because the word
 * timing covers every token of the preview, not just its first line. The
 * visibility probe's [SentenceQuote.after] is the preview's own next line for a
 * preview that spans two paragraphs, which is inside the text being searched
 * for, so it is dropped there. Null for a blank preview.
 */
internal fun wordMarkPlan(preview: String?, quote: SentenceQuote): WordMarkPlan? {
    val text = WordTokens.collapse(preview.orEmpty())
    if (text.isEmpty()) return null
    val multiLine = preview.orEmpty().lines().count { it.isNotBlank() } > 1
    return WordMarkPlan(
        quote = SentenceQuote(quote.before, text, if (multiLine) null else quote.after),
        tokenRanges = WordTokens.tokenRanges(text),
    )
}

/**
 * JavaScript for `ReaderActivity`'s word mark set-up: finds the whole
 * [quote] in the current chapter's document (the same search, and the same
 * choice among repeated copies by [before] and [after], as
 * [sentenceVisibilityScript]), builds one DOM `Range` per entry of
 * [tokenRanges] and stores them in `window.__tandemWordMark`. Returns how many
 * ranges it built, or 0 when the sentence is not on this page or the WebView
 * has no `CSS.highlights`.
 *
 * [tokenRanges] are offsets in the whitespace-collapsed quote; they are mapped
 * through the same "space before punctuation" squash the search compares on, so
 * a sentence the server tokenized as `Name : text` still lands on `Name: text`.
 */
internal fun wordMarkLocateScript(
    quote: String,
    before: String?,
    after: String?,
    tokenRanges: List<IntRange>,
): String {
    val ranges = tokenRanges.joinToString(",", "[", "]") { "[${it.first},${it.last}]" }
    return """
(function(quote, before, after, ranges) {
    if (typeof CSS === 'undefined' || !CSS.highlights || typeof Highlight === 'undefined') return 0;
    window.__tandemWordMark = null;
$pageSearchPrelude
    var sq = squash(collapse(quote));
    var full = sq.text;
    if (!full) return 0;
    var ctxBefore = squash(collapse(before)).text;
    var ctxAfter = squash(collapse(after)).text;
    var best = bestOccurrence(full, full.length, ctxBefore, ctxAfter);
    if (best < 0) return 0;
    // collapsed index -> squashed index (the squash only ever drops spaces,
    // which no token range starts or ends on).
    var inv = {};
    for (var k = 0; k < sq.map.length; k++) inv[sq.map[k]] = k;
    var built = [];
    for (var t = 0; t < ranges.length; t++) {
        var a = inv[ranges[t][0]], b = inv[ranges[t][1]];
        if (a === undefined || b === undefined) return 0;
        var first = page.map[best + a], last = page.map[best + b];
        if (first === undefined || last === undefined) return 0;
        var range = document.createRange();
        range.setStart(owners[first], offsets[first]);
        range.setEnd(owners[last], offsets[last] + 1);
        built.push(range);
    }
    window.__tandemWordMark = { ranges: built };
    if (!document.getElementById('$WORD_STYLE_ID')) {
        var style = document.createElement('style');
        style.id = '$WORD_STYLE_ID';
        style.textContent = '::highlight($WORD_HIGHLIGHT_NAME){background-color: rgba(255,193,7,0.67)}';
        (document.head || document.documentElement).appendChild(style);
    }
    return built.length;
})(${jsString(quote)}, ${jsString(before)}, ${jsString(after)}, $ranges)
""".trimIndent()
}

/**
 * Paints token [index] of the located sentence and answers where its range
 * sits relative to the viewport (issue #841): `'visible'` when any of its
 * rects is in the current column (the same test [sentenceVisibilityScript]
 * uses), `'right'` when every rect lies past the right edge (the word is on
 * the next page), `'left'` when every rect lies before the left edge, and
 * `'none'` when there is nothing to paint or measure, or on any exception.
 */
internal fun wordMarkSetScript(index: Int): String = """
(function(i) {
    try {
        var m = window.__tandemWordMark;
        if (!m || i < 0 || i >= m.ranges.length || !CSS.highlights) return 'none';
        CSS.highlights.set('$WORD_HIGHLIGHT_NAME', new Highlight(m.ranges[i]));
        var vpW = window.innerWidth;
        var rects = m.ranges[i].getClientRects();
        var seen = 0, allRight = true, allLeft = true;
        for (var r = 0; r < rects.length; r++) {
            if (!(rects[r].width > 0)) continue;
            seen++;
            if (rects[r].left >= -1 && rects[r].left < vpW) return 'visible';
            if (!(rects[r].left >= vpW)) allRight = false;
            if (!(rects[r].right <= 0)) allLeft = false;
        }
        if (seen === 0) return 'none';
        if (allRight) return 'right';
        if (allLeft) return 'left';
        return 'none';
    } catch (e) { return 'none'; }
})($index)
""".trimIndent()

/** Removes the word mark. */
internal fun wordMarkClearScript(): String = """
(function() { try { CSS.highlights.delete('$WORD_HIGHLIGHT_NAME'); } catch (e) {} })()
""".trimIndent()
