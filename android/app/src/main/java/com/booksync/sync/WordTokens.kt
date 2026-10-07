package com.booksync.sync

/**
 * The word-token rule behind the read-along word mark (issue #836).
 *
 * The server stores one start time per **whitespace token** of a sync point's
 * text preview: a maximal run of characters that are not Unicode White_Space
 * (what Python's `str.split()` splits on). Punctuation stays attached to its
 * word, so `word-next` and a quoted word are each one token. The reader marks
 * the n-th token on the page, so both sides must count identically; the shared
 * golden cases in `server/tests/fixtures/sync_parity/word_tokens_cases.json`
 * pin that (`WordTokensParityTest`).
 *
 * The set is spelled out rather than taken from `Char.isWhitespace()` or
 * Java's `\s`: the first leaves out the no-break spaces, the second is
 * ASCII-only unless `(?U)` is set.
 */
object WordTokens {

    /** Unicode White_Space. */
    fun isSpace(c: Char): Boolean = when (c.code) {
        in 0x09..0x0D, 0x20, 0x85, 0xA0, 0x1680, in 0x2000..0x200A,
        0x2028, 0x2029, 0x202F, 0x205F, 0x3000 -> true
        else -> false
    }

    /** What JavaScript's `\s` matches: White_Space minus U+0085, plus U+FEFF. */
    private fun isJsSpace(c: Char): Boolean = (isSpace(c) && c.code != 0x85) || c.code == 0xFEFF

    /** The whitespace tokens of [text], in order. */
    fun split(text: String): List<String> =
        tokenRanges(text).map { text.substring(it.first, it.last + 1) }

    /** Start..end-inclusive character offsets of every token of [text]. */
    fun tokenRanges(text: String): List<IntRange> {
        val out = ArrayList<IntRange>()
        var i = 0
        while (i < text.length) {
            if (isSpace(text[i])) {
                i++
                continue
            }
            val start = i
            while (i < text.length && !isSpace(text[i])) i++
            out.add(start until i)
        }
        return out
    }

    /** Offsets of token [index] in [text], or an empty range when there is no such token. */
    fun charRangeOfToken(text: String, index: Int): IntRange =
        tokenRanges(text).getOrNull(index) ?: IntRange.EMPTY

    /**
     * [text] the way the page script's `collapse` sees it:
     * `s.replace(/\s+/g, ' ').trim()`. Token offsets for the page search are
     * computed on this string, so the two stay in step.
     */
    fun collapse(text: String): String {
        val sb = StringBuilder()
        var pendingSpace = false
        for (c in text) {
            if (isJsSpace(c)) {
                pendingSpace = sb.isNotEmpty()
            } else {
                if (pendingSpace) sb.append(' ')
                pendingSpace = false
                sb.append(c)
            }
        }
        return sb.toString()
    }

    /** The comma-joined form kept in `sync_point_words.wordStarts`. */
    fun joinStarts(starts: List<Int>): String = starts.joinToString(",")

    /** Inverse of [joinStarts]; an empty or malformed column gives an empty array. */
    fun parseStarts(joined: String): IntArray {
        if (joined.isEmpty()) return IntArray(0)
        val parts = joined.split(',')
        val out = IntArray(parts.size)
        for ((i, p) in parts.withIndex()) out[i] = p.toIntOrNull() ?: return IntArray(0)
        return out
    }
}
