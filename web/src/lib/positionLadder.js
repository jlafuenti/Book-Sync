/**
 * The restore ladder — web half.
 *
 * Mirrors `server/services/position_resolver.py`. Both are driven by the same
 * golden vectors (`server/tests/fixtures/sync_parity/restore_cases.json`), so
 * the two implementations cannot drift silently.
 *
 * The invariant this exists to enforce: **a position holding any anchor always
 * produces at least one step.** Treating "no precise hint" as "no position" is
 * what opened a book at page one and let the next autosave overwrite a real
 * position with chapter 0.
 */

// Below this, a hint captured at a different audio position is assumed to
// still describe the same page.
export const LOCATOR_REUSE_THRESHOLD_MS = 30000

// Shortest preview worth searching for; below this the text matches too much.
export const MIN_SEARCHABLE_PREVIEW = 10

// Readium locators encode how one device rendered a page; epub.js CFIs are
// derived from the EPUB DOM and so are portable between browsers.
export const HINT_DEVICE_SCOPED = {
    readium_locator: true,
    epubjs_cfi: false,
}

function usableHint(position, deviceId, hintKind) {
    const revision = position.anchor_revision
    for (const hint of position.hints || []) {
        if (hint.kind !== hintKind) continue
        // A hint captured at a superseded anchor is stale. It is skipped, never
        // deleted — its device makes it current again by re-capturing.
        if (hint.anchor_revision !== revision) continue
        if (HINT_DEVICE_SCOPED[hintKind] !== false && hint.device_id !== deviceId) continue
        if (!hint.value) continue
        const hintAudio = hint.audio_position_ms
        if (position.source === 'audiobook' && hintAudio != null) {
            const now = position.audio_position_ms
            if (now != null && Math.abs(now - hintAudio) >= LOCATOR_REUSE_THRESHOLD_MS) continue
        }
        return hint
    }
    return null
}

/**
 * Ordered restore steps for `position`, best first.
 *
 * An empty array means "genuinely no position, open at the start" and is only
 * correct when the record holds no anchor at all.
 */
export function planRestore(position, { spineCount, deviceId, hintKind }) {
    if (!position) return []

    const steps = []

    const hint = usableHint(position, deviceId, hintKind)
    if (hint) steps.push({ kind: 'hint', value: hint.value })

    const audioMs = position.audio_position_ms
    const audioStep = (audioMs != null && audioMs > 0)
        ? { kind: 'audio', audioPositionMs: audioMs }
        : null

    // When the audiobook is the live format, its position is the only coordinate
    // known to be current (issue #479). Only a reader save refreshes
    // epub_chapter / epub_text_preview / epub_progress_percent, so after a few
    // hours of listening they describe wherever the book was last *read* — and
    // they still resolve, so trying them first opens the book there and the save
    // that follows can write it back over the real position.
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
    const listening = position.source === 'audiobook' && audioStep != null
    if (listening) steps.push(audioStep)

    const preview = (position.epub_text_preview || '').trim()
    const chapter = position.epub_chapter
    const chapterNavigable = chapter != null && chapter >= 0 && chapter < spineCount

    if (preview.length >= MIN_SEARCHABLE_PREVIEW) {
        steps.push({
            kind: 'text',
            text: preview,
            seedChapter: chapterNavigable ? chapter : null,
        })
    }

    if (chapterNavigable) steps.push({ kind: 'chapter', chapter })

    const percent = position.epub_progress_percent
    // 0% is indistinguishable from an unread book, so it is not an anchor.
    if (percent != null && percent > 0) steps.push({ kind: 'percent', percent })

    if (audioStep != null && !listening) steps.push(audioStep)

    return steps
}

/**
 * Whether the reader is still sitting at the very start of the book.
 *
 * Missing values count as the start (fail safe): a write we cannot prove is
 * off page one must be treated as page one, because writing chapter 0 over a
 * real anchor is the exact data-loss bug the write gate exists to prevent.
 */
export function isStartOfBook({ spineIndex, chapterProgression }) {
    return (spineIndex ?? 0) <= 0 && (chapterProgression ?? 0) <= 0
}

/**
 * The contract's navigation clause (§ The write gate) — web mirror of
 * Android's `PositionSavePolicy.verdictForSave`.
 *
 * A deliberate user navigation makes the current position the truth, so an
 * unresolved restore stops blocking saves once the user turns a page — EXCEPT
 * while the view still sits at the start of the book (the start-of-book
 * backstop): no navigation signal is trustworthy enough to let a start-of-book
 * write replace a real anchor, and a genuine forward page-turn moves off the
 * start on its own.
 *
 * `userNavigated` must reflect actual user input (next/prev, keyboard, TOC) —
 * never `relocated` emissions from the restore or the text-nav pass.
 */
export function navigationEstablishesPosition({ userNavigated, spineIndex, chapterProgression }) {
    if (!userNavigated) return false
    return !isStartOfBook({ spineIndex, chapterProgression })
}

/**
 * Whether `position` records a place in the book at all.
 *
 * Deliberately separate from executing the ladder: it distinguishes "we don't
 * know where you were" (block saving, offer a retry) from "you hadn't started"
 * (open at the beginning, saving is fine).
 */
export function hasAnchor(position) {
    return planRestore(position, {
        spineCount: Number.MAX_SAFE_INTEGER,
        deviceId: '',
        hintKind: '',
    }).length > 0
}
