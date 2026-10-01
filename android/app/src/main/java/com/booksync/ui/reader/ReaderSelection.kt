package com.booksync.ui.reader

import com.booksync.R
import com.booksync.data.repository.BookSyncRepository

/**
 * The decision half of the reader's text-selection toolbar (issue #227):
 * which floating-toolbar items are kept, which text "Define" looks up, and
 * what "Sync to Audio" on a selection writes.
 *
 * Plain Kotlin on purpose, so `ReaderSelectionTest` can pin it. The WebView
 * and ActionMode plumbing that *acts* on these decisions is
 * [ReaderSelectionController].
 *
 * Until issue #582, the toolbar tracked selection itself: JavaScript injected
 * into "the" Readium WebView parked the last non-trivial selection so a menu
 * click could read it back later. Readium keeps three chapter WebViews alive
 * (previous/current/next) and that JS was installed via `findWebView`, which
 * returns the first one in the view tree — the chapter the reader just left
 * after any adjacent-chapter move, not the one on screen. Define then read an
 * empty or stale selection. `EpubNavigatorFragment.currentSelection()`
 * (Readium's own `SelectableNavigator` API) always targets the resource
 * actually on screen, so the reader now asks it at tap time instead of
 * keeping a cache — see `ReaderActivity.defineSelectedWord` /
 * `syncSelectedTextToAudio`. There is nothing left here to fall back to.
 */

/** Below this many characters a selection is too little to match against the sync map. */
const val MIN_SYNC_SELECTION_CHARS = 5

/**
 * The text Define acts on, given the navigator's current selection
 * (`EpubNavigatorFragment.currentSelection()?.locator?.text?.highlight`) at
 * the moment the user tapped Define. That call already targets the resource
 * on screen, so there is no earlier or cached selection to fall back to: a
 * null or blank current selection means nothing is selected right now, full
 * stop, and yields "" — the caller's cue for the existing "Select a word to
 * define" toast. A short selection (down to a single character) is returned
 * as-is; [firstDefinableToken] decides what is actually a definable word.
 */
internal fun defineSelectionText(currentSelectionHighlight: String?): String =
    currentSelectionHighlight?.trim().orEmpty()

/** One item of the floating selection toolbar, as far as the allow-list cares. */
data class SelectionMenuItem(val id: Int, val title: String?)

/**
 * Ids of the items to remove from the floating selection toolbar. It is an
 * allow-list (issue #772): Copy (`android.R.id.copy`, so users can still quote
 * a passage) and our own injected actions (Define, Sync to Audio, Read along)
 * are kept, and EVERY other item is removed, whatever its id or title.
 *
 * The system populates the toolbar dynamically (some items on Android 14+
 * from text classification, others per OEM), so a deny-list of known noise
 * lets each new system or vendor item leak in until someone notices. Read
 * aloud is removed on purpose: it speaks the selection over the book's own
 * audio, and Read along is the replacement.
 *
 * Matching is by id only; titles are never consulted, so a localised or
 * renamed item cannot slip through either way.
 */
internal fun selectionNoiseItemIds(items: List<SelectionMenuItem>): List<Int> {
    val keptIds = setOf(
        android.R.id.copy,
        R.id.action_define,
        R.id.action_sync_selection,
        R.id.action_read_along_selection,
    )
    return items.filter { it.id !in keptIds }.map { it.id }
}

/** Soft hyphen and the zero-width characters: invisible, never part of a lookup. */
private val INVISIBLE_CHARS = Regex("[\u00AD\u200B\u200C\u200D\u2060\uFEFF]")

/** Non-breaking spaces: whitespace for word-splitting purposes, not letters. */
private val NBSP_CHARS = Regex("[\u00A0\u202F]")

/**
 * The word "Define" looks up: the first whitespace-separated token of the
 * selection with leading/trailing punctuation shorn off (apostrophes and
 * hyphens are part of a word). Empty when there is no such word.
 *
 * Normalises three things dictionaryapi.dev otherwise 404s on (issue #582),
 * none visible in plain text so cheap to always apply:
 *  - a curly quote (U+2018/U+2019) folds to a straight apostrophe, so a
 *    contraction or possessive written with one is looked up in its plain
 *    ASCII spelling, and a leading curly quote is trimmed away like any
 *    other boundary punctuation once it is no longer treated as a letter;
 *  - soft hyphens and zero-width characters are stripped from inside the
 *    word entirely, not just at the edges;
 *  - non-breaking spaces act as word separators like any other whitespace,
 *    so a selection joined by one still yields just its first word.
 * A trailing possessive "'s" is then dropped - dictionaryapi.dev has no
 * entry for the possessive form of a headword, only the word itself.
 */
internal fun firstDefinableToken(selection: String): String {
    val normalized = selection
        .replace(INVISIBLE_CHARS, "")
        .replace(NBSP_CHARS, " ")
        .replace('\u2018', '\'')
        .replace('\u2019', '\'')
    val word = normalized.trim().split(Regex("\\s+"))
        .firstOrNull()
        ?.trim { !it.isLetter() && it != '\'' && it != '-' }
        .orEmpty()
    return if (word.length > 2 && word.endsWith("'s", ignoreCase = true)) {
        word.dropLast(2)
    } else {
        word
    }
}

/** Whether [selectedText] is too little to match against the sync map. */
internal fun selectionTooShortToSync(selectedText: String): Boolean =
    selectedText.trim().length < MIN_SYNC_SELECTION_CHARS

/** What "Sync to Audio" on a selection found. */
sealed class SelectionSyncOutcome {
    object NoMatch : SelectionSyncOutcome()
    data class Matched(val audioMs: Int) : SelectionSyncOutcome()
}

/**
 * Match the selected text, in the chapter the reader is showing, to a sync
 * point and — on a match — record the handoff exactly as the page path does
 * ([PageAudioHandoff]), so the two cannot drift apart again (issue #114).
 *
 * [beforeWrite] runs once a match is known and *before* the handoff is
 * written: the reader uses it to flag that a deliberate audio match is in
 * flight, so an autosave landing mid-write cannot resolve its own sync-point
 * guess over it (see `ReaderPositionSnapshot.skipSyncPointLookup`).
 */
internal suspend fun syncSelectionToAudio(
    repository: BookSyncRepository,
    pairId: Int,
    chapterIndex: Int,
    locatorJson: String?,
    selectedText: String,
    beforeWrite: (audioMs: Int) -> Unit = {},
): SelectionSyncOutcome {
    val audioMs = repository.epubToAudioText(pairId, chapterIndex, selectedText)
    if (audioMs <= 0) return SelectionSyncOutcome.NoMatch
    beforeWrite(audioMs)
    PageAudioHandoff.apply(
        repository = repository,
        pairId = pairId,
        chapterIndex = chapterIndex,
        locatorJson = locatorJson,
        audioMs = audioMs,
    )
    return SelectionSyncOutcome.Matched(audioMs)
}
