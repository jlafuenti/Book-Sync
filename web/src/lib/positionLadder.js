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

    const audioMs = position.audio_position_ms
    if (audioMs != null && audioMs > 0) {
        steps.push({ kind: 'audio', audioPositionMs: audioMs })
    }

    return steps
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
