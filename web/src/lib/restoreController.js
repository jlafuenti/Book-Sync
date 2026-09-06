/**
 * Opening a book at the position the record describes (issue #278).
 *
 * This is the web half of the contract's "did we land?" question
 * (`docs/position-sync-contract.md`, "The write gate"): the canonical
 * position fetch with its offline fallback, the ladder walk (`planRestore`
 * is the shared plan; `executeRestore` takes the first rung that lands), the
 * verdict on whether the reader is now somewhere the record describes, and
 * the gate that verdict sets — the one thing standing between a failed
 * restore sitting on page one and an autosave that persists chapter 0 over a
 * real position from another device.
 *
 * `EbookReader` runs `restorePosition` inside the epub.js open, applies the
 * outcome to its `createWriteGate()`, and only then attaches its `relocated`
 * handler, so the save path can never run before the verdict is in.
 */

import { getPosition, audioToEpub, getDeviceId } from '../api'
import { planRestore, hasAnchor, navigationEstablishesPosition } from './positionLadder'
import { debugWarn } from './debug'

/**
 * Walk the restore ladder, taking the first step that actually lands.
 *
 * Returns true when the reader is at a position the record describes, false
 * when every step failed. False is *not* "start of book": the caller keeps
 * saving blocked, because a book showing page one after a failed restore is
 * exactly what overwrote a real position with chapter 0.
 */
export async function executeRestore(book, rendition, steps) {
    for (const step of steps) {
        try {
            if (step.kind === 'hint') {
                await rendition.display(step.value)
                return true
            }
            if (step.kind === 'chapter') {
                const item = book.spine?.items?.[step.chapter]
                if (!item) continue
                await rendition.display(item.href)
                return true
            }
            if (step.kind === 'percent') {
                const cfi = book.locations?.cfiFromPercentage?.(step.percent / 100)
                if (!cfi) continue
                await rendition.display(cfi)
                return true
            }
            // 'text' is refined after the first relocated event (it needs the
            // rendered DOM to search); 'audio' needs the sync map. Neither can
            // land the initial display on its own, so they fall through here.
        } catch (e) {
            debugWarn(`[EbookReader] restore step '${step.kind}' failed:`, e?.message || e)
        }
    }
    if (steps.length === 0) {
        // Genuinely unread — start of book is the right answer.
        await rendition.display()
        return true
    }
    await rendition.display()
    return false
}

/**
 * The record to restore from when the server cannot be reached: the portable
 * anchor the caller passed in. No CFI hint is threaded through any more —
 * callers used to pass one read from `user_progress.epub_cfi`, a mirror
 * column that no longer exists (issue #102), and a page-load snapshot was
 * staler than the fetch anyway.
 */
export function fallbackPosition({ initialChapter, initialTextPreview } = {}) {
    return initialChapter != null
        ? {
            anchor_revision: 0,
            epub_chapter: initialChapter,
            epub_text_preview: initialTextPreview || undefined,
            hints: [],
        }
        : null
}

/**
 * The canonical position at open. Reading a snapshot taken when the *page*
 * loaded meant a position set on another device in the meantime was never
 * seen.
 */
export function fetchOpeningPosition({ pairId, ebookId, initialChapter, initialTextPreview }) {
    const scope = pairId ? 'pair' : 'ebook'
    return getPosition(scope, pairId || ebookId).catch(e => {
        debugWarn('[EbookReader] position fetch failed, using props:', e.message || e)
        return fallbackPosition({ initialChapter, initialTextPreview })
    })
}

/**
 * What the ladder walk means for the write gate.
 *
 *   landed        a rung from the record resolved (or the record was empty):
 *                 established, and the landing is genuine.
 *   unresolved    nothing resolved — saving stays blocked and the reader is
 *                 told. An ANCHORED record whose plan came out empty (e.g. a
 *                 re-parsed EPUB left `epub_chapter` past this spine, with
 *                 nothing else to fall back on) is unresolved too: planning
 *                 drops the un-navigable rung, and treating the empty plan as
 *                 "unread" was exactly the silent gate-open that let page one
 *                 overwrite a real position (issue #159).
 *   provisional   the chapter + preview came from the SYNC MAP, not the
 *                 record, and the map's chapter axis can be offset from the
 *                 spine. Displaying the derived chapter is a guess — only the
 *                 text-nav pass actually finding the preview confirms it.
 *                 Treating the guess as landed is what let settle relocations
 *                 write the Copyright page (and a matcher-minted audio
 *                 position) over a real 10-minute listening position.
 *   unconfirmable a derived chapter with no searchable preview: nothing can
 *                 ever confirm the guess, so it stays unresolved.
 *
 * `positionEstablished` / `restoreLanded` are the gate flags this sets;
 * `unresolved` is what the reader shows.
 */
export function classifyLanding({ landed, steps, position, audioDerived }) {
    const unresolved = !landed || (steps.length === 0 && hasAnchor(position))
    const derivedPreview = (position?.epub_text_preview || '').trim()
    if (!unresolved && audioDerived && derivedPreview) {
        return { kind: 'provisional', positionEstablished: false, restoreLanded: false, unresolved: false }
    }
    if (!unresolved && audioDerived) {
        return { kind: 'unconfirmable', positionEstablished: false, restoreLanded: false, unresolved: true }
    }
    return unresolved
        ? { kind: 'unresolved', positionEstablished: false, restoreLanded: false, unresolved: true }
        : { kind: 'landed', positionEstablished: true, restoreLanded: true, unresolved: false }
}

/**
 * Fetch the record, plan and walk the ladder, and say what happened.
 *
 * Resolves to `{ position, steps, outcome }` — `position` is the record the
 * reader opened with (after the audio rung, if taken), `outcome` is
 * `classifyLanding`'s verdict — or to `null` when `isDestroyed()` reported
 * the book torn down mid-way, in which case nothing was displayed.
 */
export async function restorePosition({
    book, rendition, pairId, ebookId, initialChapter, initialTextPreview,
    isDestroyed = () => false,
}) {
    let position = await fetchOpeningPosition({ pairId, ebookId, initialChapter, initialTextPreview })
    if (isDestroyed()) return null

    const ladderContext = {
        spineCount: book.spine?.items?.length ?? 0,
        deviceId: getDeviceId(),
        hintKind: 'epubjs_cfi',
    }
    let steps = planRestore(position, ladderContext)

    // The audio rung (ladder step 5) — a pair only ever LISTENED to has a
    // record with `audio_position_ms` and no ebook anchor. Android executes
    // this rung through its cached sync map; the web has none, so it asks
    // the server (`audio_to_epub`, issue #159) and re-plans from the resolved
    // chapter + preview, which then seed the chapter display and the
    // text-nav pass. Audio is always planned last, so it being FIRST means
    // it is the only rung.
    let audioDerived = false
    if (pairId && steps.length > 0 && steps[0].kind === 'audio') {
        const resolved = await audioToEpub(pairId, steps[0].audioPositionMs)
            .catch(() => null)
        if (isDestroyed()) return null
        if (resolved) {
            position = {
                ...position,
                epub_chapter: resolved.epub_chapter,
                epub_text_preview: resolved.preview || position.epub_text_preview,
            }
            steps = planRestore(position, ladderContext)
            audioDerived = true
        }
    }

    const landed = await executeRestore(book, rendition, steps)
    return { position, steps, outcome: classifyLanding({ landed, steps, position, audioDerived }) }
}

/**
 * The write gate, as one object the reader keeps in a ref.
 *
 *   positionEstablished  Nothing may be saved until this is true: the
 *                        restore landed, the record was genuinely empty, or
 *                        the user turned a page (the navigation clause).
 *   restoreLanded        True only when the restore GENUINELY landed. The
 *                        navigation clause reopening the gate does NOT set
 *                        it — saves after an unconfirmed landing must never
 *                        carry matcher-derived fields (audio_position_ms
 *                        above all): in production the matcher "upgraded"
 *                        wrong copyright-page text and overwrote a 600000ms
 *                        listening position with 1530ms.
 *   audioConfirmPending  Set while an audio-derived restore awaits
 *                        confirmation from the text-nav pass.
 *   userNavigated        The user deliberately navigated (next/prev,
 *                        keyboard, TOC) since open — never set from
 *                        `relocated` events, which the restore and the
 *                        text-nav pass also emit (issue #159).
 *
 * The transitions return whether they changed anything, so the reader knows
 * when the unresolved banner must follow. `maybeOpen` answers 'open' (was
 * already), 'opened' (the navigation clause just opened it) or 'closed'.
 */
export function createWriteGate() {
    const gate = {
        positionEstablished: false,
        restoreLanded: false,
        audioConfirmPending: false,
        userNavigated: false,
    }

    gate.applyLanding = (outcome) => {
        if (outcome.kind === 'provisional') gate.audioConfirmPending = true
        gate.positionEstablished = outcome.positionEstablished
        gate.restoreLanded = outcome.restoreLanded
    }

    // The text-nav pass confirmed an audio-derived landing: the preview text
    // was actually located in this book.
    gate.confirmProvisionalLanding = () => {
        if (!gate.audioConfirmPending) return false
        gate.audioConfirmPending = false
        gate.positionEstablished = true
        gate.restoreLanded = true
        return true
    }

    // The text-nav pass could not find the preview anywhere: the audio rung
    // did NOT land. Unresolved semantics — banner, gate stays closed.
    gate.failProvisionalLanding = () => {
        if (!gate.audioConfirmPending) return false
        gate.audioConfirmPending = false
        return true
    }

    gate.noteUserNavigation = () => {
        gate.userNavigated = true
    }

    // The contract's navigation clause: a deliberate user page-turn (off the
    // start of the book) makes the current position the truth.
    gate.maybeOpen = ({ spineIndex, chapterProgression }) => {
        if (gate.positionEstablished) return 'open'
        if (navigationEstablishesPosition({
            userNavigated: gate.userNavigated, spineIndex, chapterProgression,
        })) {
            gate.positionEstablished = true
            return 'opened'
        }
        return 'closed'
    }

    return gate
}
