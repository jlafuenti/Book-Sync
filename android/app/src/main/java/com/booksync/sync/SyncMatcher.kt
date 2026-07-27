package com.booksync.sync

/**
 * The only properties the matcher reads off a sync point. Keeping this narrow is what
 * lets the algorithm stay pure (no Room, no Android) and therefore JVM-unit-testable.
 */
interface MatchablePoint {
    val epubChapter: Int
    val epubSentenceIndex: Int
    val epubTextPreview: String?
    /** Alignment confidence 0..1; 0 means interpolated (not directly matched). */
    val confidence: Float
}

/**
 * Pure, dependency-free ebook→audio matching (issue #41).
 *
 * This is hand-mirrored from `server/services/sync_matcher.py` so Android can match
 * offline while still producing exactly the same answer as the server (and therefore
 * the web reader). Both halves — `normalizeForSearch` and the full `match` — are pinned
 * to the shared golden vectors in `server/tests/fixtures/sync_parity/`, enforced here by
 * SyncMatcherParityTest / MatchParityTest and on the server by tests/test_sync_matching.py.
 *
 * **Any change here must change the Python side and the fixtures in the same PR.**
 */
object SyncMatcher {

    /** Prefix lengths tried by the exact pass, longest first. */
    private val SEARCH_LENGTHS = listOf(200, 150, 100, 60, 30)
    /** Chars skipped by the exact pass's second attempt (drops a chapter heading). */
    private const val HEADING_SKIP = 30
    /** Length of the needle handed to the fuzzy pass. */
    private const val FUZZY_NEEDLE_LEN = 150
    /** Minimum Dice similarity for the fuzzy pass to accept a window. */
    const val FUZZY_THRESHOLD = 0.60

    /** Normalize text for comparison: lowercase, convert ALL whitespace to spaces, strip punctuation. */
    fun normalizeForSearch(text: String): String {
        return text.lowercase()
            // Convert newlines and tabs to spaces FIRST (before stripping non-alphanumeric)
            .replace('\n', ' ')
            .replace('\r', ' ')
            .replace('\t', ' ')
            // Convert Unicode whitespace variants to regular spaces
            .replace('\u00A0', ' ')  // non-breaking space (very common in epubs)
            .replace('\u2002', ' ')  // en space
            .replace('\u2003', ' ')  // em space
            .replace('\u2009', ' ')  // thin space
            .replace('\u200B', ' ')  // zero-width space
            .replace('\u202F', ' ')  // narrow no-break space
            .replace(Regex("[^a-z0-9 ]"), "") // Keep ONLY a-z, digits, regular space
            .replace(Regex(" +"), " ")        // Collapse multiple spaces
            .trim()
    }

    /** Character-bigram set of a normalized string, encoded as Ints for speed. */
    internal fun bigramSet(s: String): HashSet<Int> {
        val set = HashSet<Int>(maxOf(16, s.length))
        for (i in 0 until s.length - 1) set.add(s[i].code * 1024 + s[i + 1].code)
        return set
    }

    /** Dice coefficient between two bigram sets: 2*|A∩B| / (|A|+|B|), 0..1. */
    internal fun diceSimilarity(a: HashSet<Int>, b: HashSet<Int>): Double {
        if (a.isEmpty() || b.isEmpty()) return 0.0
        val (small, large) = if (a.size <= b.size) a to b else b to a
        var inter = 0
        for (x in small) if (x in large) inter++
        return 2.0 * inter / (a.size + b.size)
    }

    /**
     * Sliding-window fuzzy search: find the offset in [transcript] whose window
     * best matches [needle] by bigram Dice similarity. Returns (charOffset, score)
     * or null if below [threshold]. Tolerates transcription wording differences
     * that defeat exact substring search (mishears, "Mr." vs "mister", etc).
     */
    internal fun fuzzyFindInTranscript(
        transcript: String,
        needle: String,
        threshold: Double = FUZZY_THRESHOLD,
    ): Pair<Int, Double>? {
        if (needle.length < 20 || transcript.length < needle.length) return null
        val needleBigrams = bigramSet(needle)
        val window = needle.length
        val step = maxOf(10, window / 4)
        var bestOffset = -1
        var bestScore = 0.0
        var offset = 0
        while (offset + window <= transcript.length) {
            val score = diceSimilarity(needleBigrams, bigramSet(transcript.substring(offset, offset + window)))
            if (score > bestScore) { bestScore = score; bestOffset = offset }
            offset += step
        }
        // Refine around the best coarse hit with step 1 for a tighter offset
        if (bestOffset >= 0 && bestScore >= threshold) {
            var refinedOffset = bestOffset
            var refinedScore = bestScore
            val lo = maxOf(0, bestOffset - step)
            val hi = minOf(transcript.length - window, bestOffset + step)
            for (o in lo..hi) {
                val s = diceSimilarity(needleBigrams, bigramSet(transcript.substring(o, o + window)))
                if (s > refinedScore) { refinedScore = s; refinedOffset = o }
            }
            return refinedOffset to refinedScore
        }
        return null
    }

    /**
     * If the matched point is interpolated (confidence == 0), prefer the nearest real
     * whisper-matched point within ±3 list positions — its timestamp came from the
     * transcript, not from interpolation.
     */
    internal fun <T : MatchablePoint> nudgeToConfidentPoint(points: List<T>, matchedPointIdx: Int): T {
        val matchedPoint = points[matchedPointIdx]
        if (matchedPoint.confidence > 0f) return matchedPoint
        val nearby = (maxOf(0, matchedPointIdx - 3)..minOf(points.lastIndex, matchedPointIdx + 3))
            .map { points[it] }
            .filter { it.confidence > 0.5f }
            .minByOrNull { kotlin.math.abs(it.epubSentenceIndex - matchedPoint.epubSentenceIndex) }
        return nearby ?: matchedPoint
    }

    private class ChapterTranscript<T : MatchablePoint>(
        val chapter: Int,
        val points: List<T>,
        val transcript: String,
        /** (startCharIndex, pointIndex) for each sentence that made it into the transcript. */
        val boundaries: List<Pair<Int, Int>>,
    ) {
        fun pointAtOffset(matchIndex: Int): T {
            var matchedPointIdx = 0
            for ((startPos, idx) in boundaries) {
                if (startPos <= matchIndex) matchedPointIdx = idx else break
            }
            return nudgeToConfidentPoint(points, matchedPointIdx)
        }
    }

    private fun <T : MatchablePoint> buildChapterTranscripts(
        points: List<T>,
        chapterHint: Int,
    ): List<ChapterTranscript<T>> {
        // Hint chapter first, then expanding outward: hint-1, hint+1, hint-2, hint+2, ...
        val chaptersToTry = listOf(chapterHint) + (1..10).flatMap { d -> listOf(chapterHint - d, chapterHint + d) }

        return chaptersToTry.mapNotNull { targetChapter ->
            val chapterPoints = points.filter { it.epubChapter == targetChapter }
                .sortedBy { it.epubSentenceIndex }
            if (chapterPoints.isEmpty()) return@mapNotNull null

            val builder = StringBuilder()
            val boundaries = mutableListOf<Pair<Int, Int>>()
            for ((idx, point) in chapterPoints.withIndex()) {
                val preview = point.epubTextPreview ?: continue
                val normalized = normalizeForSearch(preview)
                if (normalized.isEmpty()) continue
                boundaries.add(Pair(builder.length, idx))
                builder.append(normalized)
                builder.append(" ") // Space between sentences
            }
            val transcript = builder.toString()
            if (transcript.isEmpty()) return@mapNotNull null
            ChapterTranscript(targetChapter, chapterPoints, transcript, boundaries)
        }
    }

    /**
     * Find the sync point matching a snippet of extracted EPUB text, or null.
     *
     * Pass 1 is an exact substring search over the concatenated per-chapter transcript,
     * across every candidate chapter — an exact match anywhere beats a fuzzy one, so a
     * weak fuzzy hit in the hint chapter can never shadow the true location next door.
     * Pass 2 (only if pass 1 found nothing) is a fuzzy bigram search taking the best
     * score across all candidate chapters.
     */
    fun <T : MatchablePoint> match(points: List<T>, epubText: String, chapterHint: Int): T? {
        val normalizedEpub = normalizeForSearch(epubText)
        if (normalizedEpub.length < 10) return null

        val chapterTranscripts = buildChapterTranscripts(points, chapterHint)
        if (chapterTranscripts.isEmpty()) return null

        val searchLengths = SEARCH_LENGTHS
            .map { minOf(normalizedEpub.length, it) }
            .distinct()
            .filter { it > 10 }

        // PASS 1 — exact substring across all candidate chapters.
        for (ct in chapterTranscripts) {
            for (searchLen in searchLengths) {
                var matchIndex = ct.transcript.indexOf(normalizedEpub.take(searchLen))

                // Retry a bit into the text, in case a chapter heading leads the extract.
                if (matchIndex < 0 && normalizedEpub.length > searchLen + HEADING_SKIP) {
                    val offsetText = normalizedEpub.substring(HEADING_SKIP).take(searchLen)
                    matchIndex = ct.transcript.indexOf(offsetText)
                }

                if (matchIndex >= 0) return ct.pointAtOffset(matchIndex)
            }
        }

        // PASS 2 — fuzzy, best score across all candidate chapters.
        val needle = normalizedEpub.take(FUZZY_NEEDLE_LEN)
        var best: Triple<ChapterTranscript<T>, Int, Double>? = null
        for (ct in chapterTranscripts) {
            val hit = fuzzyFindInTranscript(ct.transcript, needle, FUZZY_THRESHOLD) ?: continue
            if (best == null || hit.second > best.third) {
                best = Triple(ct, hit.first, hit.second)
            }
        }
        return best?.let { (ct, offset, _) -> ct.pointAtOffset(offset) }
    }
}
