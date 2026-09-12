package com.booksync.data.sync

/**
 * The restore ladder — Android half.
 *
 * Mirrors `server/services/position_resolver.py` and `web/src/lib/positionLadder.js`.
 * All three are driven by the same golden vectors
 * (`server/tests/fixtures/sync_parity/restore_cases.json`), so they cannot
 * drift apart silently.
 *
 * The invariant this exists to enforce: **a position holding any anchor always
 * produces at least one step.** This reader used to treat "no Readium locator"
 * as "no position", which opened the book at page one; the autosave then
 * persisted chapter 0 over a real position set on another device.
 */

/** Below this, a hint captured at a different audio position still describes the page. */
const val LOCATOR_REUSE_THRESHOLD_MS = 30_000

/** Shortest preview worth searching for; below this the text matches too much. */
const val MIN_SEARCHABLE_PREVIEW = 10

const val HINT_READIUM_LOCATOR = "readium_locator"
const val HINT_EPUBJS_CFI = "epubjs_cfi"

/**
 * Whether a hint of this kind is only usable by the device that captured it.
 * Readium locators encode how one device rendered a page; epub.js CFIs are
 * derived from the EPUB DOM and are portable between browsers.
 */
val HINT_DEVICE_SCOPED = mapOf(
    HINT_READIUM_LOCATOR to true,
    HINT_EPUBJS_CFI to false,
)

/** A precise position offered by one device, tagged with the anchor it belongs to. */
data class PositionHint(
    val kind: String,
    val deviceId: String,
    val value: String,
    val anchorRevision: Long,
    val audioPositionMs: Int? = null,
)

/** The anchors a stored position can offer, in whatever combination it has. */
data class StoredPosition(
    val anchorRevision: Long = 0,
    val source: String? = null,
    val epubChapter: Int? = null,
    val epubSentenceIndex: Int? = null,
    val epubTextPreview: String? = null,
    val epubProgressPercent: Float? = null,
    val audioPositionMs: Int? = null,
    val hints: List<PositionHint> = emptyList(),
)

/** One rung of the ladder, carrying what the reader needs to act on it. */
sealed class RestoreStep {
    abstract val kind: String

    data class Hint(val value: String) : RestoreStep() {
        override val kind = "hint"
    }

    data class Text(val text: String, val seedChapter: Int?) : RestoreStep() {
        override val kind = "text"
    }

    data class Chapter(val chapter: Int) : RestoreStep() {
        override val kind = "chapter"
    }

    data class Percent(val percent: Float) : RestoreStep() {
        override val kind = "percent"
    }

    data class Audio(val audioPositionMs: Int) : RestoreStep() {
        override val kind = "audio"
    }
}

private fun usableHint(
    position: StoredPosition,
    deviceId: String,
    hintKind: String,
): PositionHint? {
    for (hint in position.hints) {
        if (hint.kind != hintKind) continue
        // A hint captured at a superseded anchor is stale. It is skipped, never
        // deleted — its device makes it current again by re-capturing.
        if (hint.anchorRevision != position.anchorRevision) continue
        if (HINT_DEVICE_SCOPED[hintKind] != false && hint.deviceId != deviceId) continue
        if (hint.value.isEmpty()) continue
        val hintAudio = hint.audioPositionMs
        if (position.source == "audiobook" && hintAudio != null) {
            val now = position.audioPositionMs
            if (now != null && kotlin.math.abs(now - hintAudio) >= LOCATOR_REUSE_THRESHOLD_MS) {
                continue
            }
        }
        return hint
    }
    return null
}

/**
 * Ordered restore steps for [position], best first.
 *
 * An empty list means "genuinely no position, open at the start" and is only
 * correct when the record holds no anchor at all.
 */
fun planRestore(
    position: StoredPosition?,
    spineCount: Int,
    deviceId: String,
    hintKind: String,
): List<RestoreStep> {
    if (position == null) return emptyList()

    val steps = mutableListOf<RestoreStep>()

    usableHint(position, deviceId, hintKind)?.let { steps.add(RestoreStep.Hint(it.value)) }

    val audioMs = position.audioPositionMs
    val audioStep = if (audioMs != null && audioMs > 0) RestoreStep.Audio(audioMs) else null

    // When the audiobook is the live format, its position is the only coordinate
    // known to be current (issue #479). Only a reader save refreshes
    // epubChapter / epubTextPreview / epubProgressPercent, so after a few hours
    // of listening they describe wherever the book was last *read* — and they
    // still resolve, so trying them first opens the book there and the save that
    // follows can write it back over the real position.
    //
    // `usableHint` already applies exactly this reasoning, dropping a locator
    // captured more than LOCATOR_REUSE_THRESHOLD_MS away from where the audio
    // now is. The ebook rungs are stale for the same reason; they simply carry
    // no capture-time stamp to measure it with, so the ordering carries the rule
    // instead.
    //
    // The hint still leads when it qualifies: it is freshness-checked and exact,
    // whereas the audio rung re-derives the page through the sync map and is
    // lossier. So this only changes which fallback is reached when the precise
    // answer is unavailable — the case where the page is least trustworthy.
    val listening = position.source == "audiobook" && audioStep != null
    if (listening) steps.add(audioStep!!)

    val preview = position.epubTextPreview?.trim().orEmpty()
    val chapter = position.epubChapter
    val chapterNavigable = chapter != null && chapter >= 0 && chapter < spineCount

    if (preview.length >= MIN_SEARCHABLE_PREVIEW) {
        steps.add(RestoreStep.Text(preview, if (chapterNavigable) chapter else null))
    }

    if (chapterNavigable) steps.add(RestoreStep.Chapter(chapter!!))

    val percent = position.epubProgressPercent
    // 0% is indistinguishable from an unread book, so it is not an anchor.
    if (percent != null && percent > 0f) steps.add(RestoreStep.Percent(percent))

    if (audioStep != null && !listening) steps.add(audioStep)

    return steps
}

/**
 * Whether [position] records a place in the book at all.
 *
 * Deliberately separate from executing the ladder: it distinguishes "we don't
 * know where you were" (block saving, tell the user) from "you hadn't started"
 * (open at the beginning, saving is fine).
 */
fun hasAnchor(position: StoredPosition?): Boolean =
    planRestore(position, spineCount = Int.MAX_VALUE, deviceId = "", hintKind = "").isNotEmpty()
