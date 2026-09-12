package com.booksync.ui.reader

import android.util.Log
import com.booksync.data.sync.RestoreStep

private const val TAG = "ReaderRestore"

/** Chapter length assumed for a spine item whose text could not be parsed. */
private const val FALLBACK_CHAPTER_LENGTH = 1000L

/** Leading chapter heading the web reader's preview carries but a parsed chapter does not. */
private val LEADING_HEADING = Regex("^(CHAPTER\\s+\\d+|PROLOGUE)[\\s\\n,.]*", RegexOption.IGNORE_CASE)

/**
 * Below this many spine items there is nothing to sample: the probes would
 * overlap the match itself, and "appears in most of a three-item book" says
 * nothing. See `isBoilerplate` (issue #477).
 */
private const val MIN_SPINE_FOR_BOILERPLATE_CHECK = 6

/**
 * Put an explicit player-to-reader handoff ahead of the stored ladder.
 *
 * Switching from the player says where the user *is*: the audiobook is at this
 * millisecond and they want the page that goes with it. The stored ebook
 * coordinate can be hours stale — only a reader save updates it — so trying it
 * first opens the wrong page. The reverse direction has always worked this way:
 * `ReaderActivity.syncAudioToPage` maps the page the user is on to an audio
 * position rather than trusting the stored one. This is the missing mirror of
 * it, and without it "Switch to Reader" landed on the title page of a book
 * being listened to four and a half hours in.
 *
 * Only ever *prepends*, and only for an explicit handoff: an ordinary open
 * passes 0 and gets the shared planner's ladder untouched, so nothing about
 * the cross-platform restore decision changes. A handoff that cannot resolve
 * (no sync map yet) falls through to exactly the same rungs as before.
 */
fun withHandoffAnchor(steps: List<RestoreStep>, handoffAudioMs: Int): List<RestoreStep> {
    if (handoffAudioMs <= 0) return steps
    val rest = steps.filterNot { it is RestoreStep.Audio && it.audioPositionMs == handoffAudioMs }
    return listOf(RestoreStep.Audio(handoffAudioMs)) + rest
}

/**
 * What the restore ladder needs to know about the open book, and nothing
 * else (issue #227). `ReaderActivity` implements it over a Readium
 * `Publication`; tests implement it over a list of strings.
 */
interface SpineSource {
    /** Number of reading-order items. */
    val spineCount: Int

    /**
     * Plain text of spine item [index], or null when it is out of range or
     * could not be parsed. Called repeatedly during a text search, so the
     * implementation should cache.
     */
    suspend fun plainTextAt(index: Int): String?

    /**
     * Content-weighted chapter lengths for the percent rung. The default
     * derives them from [plainTextAt]; the reader overrides it with the
     * lengths it precomputes in the background.
     */
    suspend fun chapterLengths(): LongArray =
        LongArray(spineCount) { i -> plainTextAt(i)?.length?.toLong() ?: FALLBACK_CHAPTER_LENGTH }
}

/**
 * The audio rung's view of the sync map: which chapter and sentence text a
 * playback position names. Absent for a standalone ebook, which has neither
 * a sync map nor an audiobook to have produced the position (issue #169).
 */
fun interface AudioAnchorSource {
    suspend fun audioToEpubText(audioPositionMs: Int): Pair<Int, String>
}

/** Where a rung landed, in terms the adapter turns into a Readium locator. */
sealed class RestoreTarget {
    /** A device hint, verbatim; the adapter decodes it (it already checked it can). */
    data class Hint(val value: String) : RestoreTarget()

    /**
     * A spine item and, when a rung could say, a progression into it. A null
     * [progression] is the item's own default locator — the chapter rung's
     * "top of the chapter" — and is distinct from an explicit 0.0.
     *
     * [persistAsHintForAudioMs] is set by the audio rung: the locator built
     * from this target should be stored as this device's hint for that audio
     * position, so the next open at it takes the exact-hint rung instead of
     * redoing the lossy sync-map chain.
     */
    data class Spine(
        val index: Int,
        val progression: Double?,
        val persistAsHintForAudioMs: Int? = null,
    ) : RestoreTarget()
}

/**
 * The outcome of walking a plan. [outcome] is what [PositionSavePolicy]
 * needs: an empty plan is an unread book, a plan no rung could land is
 * *unresolved*, and anything that landed is exactly that.
 */
data class RestoreResult(
    val target: RestoreTarget?,
    val landed: RestoreStep?,
    val outcome: PositionSavePolicy.RestoreOutcome,
)

/**
 * Executes the restore ladder `PositionResolver.planRestore` produces, one
 * rung at a time, first landing wins (contract § "The restore ladder").
 *
 * The *decision* — which rungs, in what order — is shared with the server and
 * the web reader and pinned to golden vectors. This is the Android
 * *execution*: searching the spine for a text preview, mapping a book
 * fraction onto chapters by length, and turning a sync-map answer into a
 * page. It used to live inside `ReaderActivity`, where coverage excluded it;
 * `ReaderRestoreExecutorTest` now drives it with the same fixture cases.
 *
 * Readium `Locator` construction stays in the activity's adapter: this class
 * never sees a `Publication`, only [SpineSource].
 *
 * @param hintDecodes whether a stored hint value decodes to something the
 *   reader can display. A hint that does not (an empty `{}`, a CFI from a
 *   browser) fails its rung and the ladder moves on.
 */
class ReaderRestoreExecutor(
    private val spine: SpineSource,
    private val hintDecodes: (String) -> Boolean = { true },
    private val audio: AudioAnchorSource? = null,
) {

    /**
     * Walk [steps] in order and stop at the first that lands.
     *
     * An exception thrown by a rung is reported as
     * [PositionSavePolicy.RestoreOutcome.Unresolved] rather than propagated:
     * the reader still opens, and the policy withholds full saves until the
     * user turns a page. [PositionSavePolicy.onRestoreOutcome] is monotonic,
     * so this can never demote a landing recorded earlier.
     */
    suspend fun resolve(steps: List<RestoreStep>): RestoreResult {
        return try {
            for (step in steps) {
                val target = executeStep(step)
                if (target != null) {
                    Log.d(TAG, "restored via '${step.kind}' -> $target")
                    return RestoreResult(target, step, PositionSavePolicy.RestoreOutcome.Landed)
                }
                Log.d(TAG, "step '${step.kind}' did not resolve")
            }
            // No steps at all means the book is genuinely unread, and opening
            // at the beginning is correct — a full save is safe. Steps that
            // all failed mean we hold a position we could not resolve — the
            // policy still allows a local metadata-only stamp (never the full
            // anchors) until the user actually turns a page.
            val outcome = if (steps.isEmpty()) {
                PositionSavePolicy.RestoreOutcome.Unread
            } else {
                Log.w(TAG, "position unresolved — full saves withheld until a page turn")
                PositionSavePolicy.RestoreOutcome.Unresolved
            }
            RestoreResult(null, null, outcome)
        } catch (e: Exception) {
            Log.w(TAG, "Error executing restore ladder", e)
            RestoreResult(null, null, PositionSavePolicy.RestoreOutcome.Unresolved)
        }
    }

    /** Try one rung of the ladder. Returns null when it doesn't resolve. */
    suspend fun executeStep(step: RestoreStep): RestoreTarget? = when (step) {
        is RestoreStep.Hint ->
            if (hintDecodes(step.value)) RestoreTarget.Hint(step.value) else null

        is RestoreStep.Text -> {
            val idx = findSpineIndexForText(step.text, step.seedChapter ?: 0)
            if (idx != null && idx in 0 until spine.spineCount) {
                RestoreTarget.Spine(idx, findTextProgressionInChapter(idx, step.text) ?: 0.0)
            } else null
        }

        is RestoreStep.Chapter ->
            if (step.chapter in 0 until spine.spineCount) RestoreTarget.Spine(step.chapter, null) else null

        is RestoreStep.Percent -> targetForProgress(step.percent / 100.0)

        // An audio rung cannot resolve without a sync map, and a standalone
        // ebook has neither one nor an audiobook to have produced the position
        // (issue #169). Guarded rather than left to return null by accident:
        // the lookup would otherwise query sync points for pair id 0.
        is RestoreStep.Audio -> {
            val source = audio
            if (source == null) null else {
                // Audio -> sync map -> preview -> the same text search as above.
                val (syncChapter, previewText) = source.audioToEpubText(step.audioPositionMs)
                val idx = findSpineIndexForText(previewText, syncChapter)
                    ?: syncChapter.takeIf { it in 0 until spine.spineCount }
                if (idx != null && idx in 0 until spine.spineCount && previewText.isNotEmpty()) {
                    RestoreTarget.Spine(
                        idx,
                        findTextProgressionInChapter(idx, previewText) ?: 0.0,
                        persistAsHintForAudioMs = step.audioPositionMs,
                    )
                } else null
            }
        }
    }

    /**
     * Which spine index contains [previewText]. Searches outward from
     * [hintIdx] — or from the middle of the book when there is no usable
     * hint — so a preview that appears in more than one chapter lands in the
     * nearest one.
     */
    suspend fun findSpineIndexForText(previewText: String, hintIdx: Int = -1): Int? {
        if (previewText.isEmpty()) return null

        // Strip leading chapter headings (e.g. "CHAPTER 28\n") since epub text has different formatting
        val stripped = previewText.replace(LEADING_HEADING, "")
        // Normalize newlines to spaces and collapse whitespace
        val searchText = stripped.replace("\n", " ").replace(Regex("\\s+"), " ").take(60).trim()

        if (searchText.length < 10) {
            Log.d(TAG, "findSpineIndexForText: search text too short after cleaning: '$searchText'")
            return null
        }

        val n = spine.spineCount
        // If we have a valid hint, search outward from it
        val hint = if (hintIdx in 0 until n) hintIdx else n / 2
        for (offset in 0 until n) {
            for (candidate in listOf(hint + offset, hint - offset).distinct()) {
                if (candidate in 0 until n) {
                    val plainText = spine.plainTextAt(candidate) ?: continue
                    if (plainText.contains(searchText, ignoreCase = true)) {
                        if (isBoilerplate(searchText, foundAt = candidate, n = n)) {
                            Log.d(TAG, "findSpineIndexForText: '${searchText.take(40)}' runs through the whole book — not an anchor")
                            return null
                        }
                        Log.d(TAG, "findSpineIndexForText: found '${searchText.take(40)}' at spine $candidate (hint=$hint)")
                        return candidate
                    }
                }
            }
        }
        Log.d(TAG, "findSpineIndexForText: no match for '${searchText.take(40)}'")
        return null
    }

    /**
     * Whether [searchText] runs through the whole book rather than naming a
     * place in it (issue #477).
     *
     * A Calibre-split EPUB repeats the title line at the top of every spine
     * file, so a stored preview of that line matches every item and the outward
     * search returns whichever happens to be nearest — in the reported case the
     * title page, from a bookmark whose real position was four and a half hours
     * into the audiobook. A confident landing there is worse than no landing:
     * the save that follows resolves a sync-point match from it, and wrote an
     * audio position of 340 ms over one of 16,348,540 ms.
     *
     * Counts how much of the spine carries the text rather than sampling it.
     * Sampling was tried first and failed on the very book that prompted this:
     * Calibre splits it into a cover, alternating title-only separator pages
     * and real chapters, so the line heads half the spine and three probes
     * caught one hit. Counting to a quarter of the book tells a running header
     * apart from a sentence that happens to recur in a chapter or two, which
     * must still resolve.
     *
     * Stops as soon as the threshold is passed, so the walk is bounded, and the
     * reader has already parsed every item by the time a restore runs.
     *
     * Runs *after* the outward search on purpose, so the seed chapter is still
     * the first item read — `ReaderRestoreExecutorTest` pins that.
     */
    private suspend fun isBoilerplate(searchText: String, foundAt: Int, n: Int): Boolean {
        if (n < MIN_SPINE_FOR_BOILERPLATE_CHECK) return false
        val limit = n / 4
        var hits = 0
        for (i in 0 until n) {
            if (i == foundAt) continue
            if (spine.plainTextAt(i)?.contains(searchText, ignoreCase = true) == true) {
                hits++
                if (hits > limit) return true
            }
        }
        return false
    }

    /**
     * Where inside spine item [chapterIndex] the preview sits, as a 0..1
     * character fraction; falls back to the first twenty characters when the
     * full preview does not match (typos, re-parsing), and null when neither
     * does.
     */
    suspend fun findTextProgressionInChapter(chapterIndex: Int, textPreview: String): Double? {
        val plainText = spine.plainTextAt(chapterIndex) ?: return null
        // Strip chapter heading and normalize whitespace
        val stripped = textPreview.replace(LEADING_HEADING, "")
        val cleanPreview = stripped.replace("\n", " ").replace(Regex("\\s+"), " ").trim().lowercase()
        val cleanPlain = plainText.replace(Regex("\\s+"), " ").lowercase()

        val index = cleanPlain.indexOf(cleanPreview)
        if (index >= 0) {
            return index.toDouble() / cleanPlain.length
        }

        // Approximate fallback if exact search misses (typos etc)
        // Just look for the first 20 characters
        val shortPreview = cleanPreview.take(20)
        val shortIndex = cleanPlain.indexOf(shortPreview)
        if (shortIndex >= 0) {
            return shortIndex.toDouble() / cleanPlain.length
        }

        return null
    }

    /**
     * Map a 0..1 book fraction onto a spine item and a progression into it,
     * weighting chapters by length. Shared by the percent rung and the
     * progress slider, which are the same mapping in two directions.
     */
    suspend fun targetForProgress(progress: Double): RestoreTarget.Spine? {
        val n = spine.spineCount
        if (n == 0) return null
        val lengths = spine.chapterLengths()
        val total = lengths.sum().coerceAtLeast(1)
        val targetChar = (progress * total).toLong().coerceIn(0, total - 1)

        var accumulated = 0L
        var spineIndex = n - 1
        var withinChapter = 1.0
        for (i in lengths.indices) {
            val len = lengths[i]
            if (accumulated + len > targetChar) {
                spineIndex = i
                withinChapter = if (len > 0) (targetChar - accumulated).toDouble() / len else 0.0
                break
            }
            accumulated += len
        }
        return RestoreTarget.Spine(spineIndex, withinChapter.coerceIn(0.0, 1.0))
    }
}
