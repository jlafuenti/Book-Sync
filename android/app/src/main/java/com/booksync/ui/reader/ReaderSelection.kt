package com.booksync.ui.reader

import com.booksync.R
import com.booksync.data.repository.BookSyncRepository

/**
 * The decision half of the reader's text-selection toolbar (issue #227):
 * what the injected JavaScript reports, how the bridge's answer is decoded,
 * which floating-toolbar items are noise, which word "Define" looks up, and
 * what "Sync to Audio" on a selection writes.
 *
 * Plain Kotlin on purpose, so `ReaderSelectionTest` can pin it. The WebView
 * and ActionMode plumbing that *acts* on these decisions is
 * [ReaderSelectionController].
 */

/**
 * Injected into the Readium host WebView on every page turn.
 * Listens for text selection in all epub iframes and stores the last selection in
 * window.top._bookSyncSelection so it survives ActionMode dismissal.
 * Readium serves epub content from localhost iframes, so same-origin access works.
 */
const val SELECTION_TRACKER_JS = """
    (function() {
        function installInDoc(doc) {
            if (!doc || doc._bsListenerAdded) return;
            doc._bsListenerAdded = true;
            doc.addEventListener('selectionchange', function() {
                try {
                    var sel = doc.defaultView.getSelection();
                    var text = sel ? sel.toString().trim() : '';
                    if (text.length > 3) { window.top._bookSyncSelection = text; }
                } catch(e) {}
            });
        }
        installInDoc(document);
        var frames = document.querySelectorAll('iframe');
        for (var i = 0; i < frames.length; i++) {
            try {
                installInDoc(frames[i].contentDocument);
                frames[i].addEventListener('load', (function(f) {
                    return function() { try { installInDoc(f.contentDocument); } catch(e) {} };
                })(frames[i]));
            } catch(e) {}
        }
        new MutationObserver(function(ms) {
            ms.forEach(function(m) {
                m.addedNodes.forEach(function(n) {
                    if (n.nodeName === 'IFRAME') {
                        n.addEventListener('load', function() {
                            try { installInDoc(n.contentDocument); } catch(e) {}
                        });
                    }
                });
            });
        }).observe(document.body || document, {childList: true, subtree: true});
    })()
"""

/**
 * Reads the selection the tracker parked. `window.getSelection()` here is
 * unreliable (the ActionMode may have cleared the DOM selection by the time
 * this evaluates), so `window._bookSyncSelection`, captured the moment the
 * user selected, is preferred; the frames are only asked when it is empty.
 */
const val CAPTURE_SELECTION_JS = """
    (function() {
        var stored = window._bookSyncSelection || '';
        if (stored.trim().length > 3) return stored;
        var frames = document.querySelectorAll('iframe');
        for (var i = 0; i < frames.length; i++) {
            try {
                var sel = frames[i].contentWindow.getSelection().toString().trim();
                if (sel.length > 3) return sel;
            } catch(e) {}
        }
        return window.getSelection().toString();
    })()
"""

/** Below this many characters a selection is too little to match against the sync map. */
const val MIN_SYNC_SELECTION_CHARS = 5

/**
 * Decode what `evaluateJavascript` hands back for [CAPTURE_SELECTION_JS]: a
 * JSON string literal, whose quotes are stripped and whose escaped newlines
 * become spaces. Empty when nothing was captured.
 */
internal fun decodeCapturedSelection(raw: String?): String =
    raw?.trim('"')?.replace("\\n", " ")?.trim() ?: ""

/** One item of the floating selection toolbar, as far as the noise rule cares. */
data class SelectionMenuItem(val id: Int, val title: String?)

/**
 * Ids of the items to remove from the floating selection toolbar: Web
 * Search / Select All / Share / Translate / Assist. Copy (`android.R.id.copy`)
 * is kept so users can still quote a passage, anything unrecognised is left
 * alone so accessibility items like "Read Aloud" stay available, and our own
 * injected actions are never touched.
 *
 * The system populates these items dynamically (some on Android 14+ from
 * text classification), so they are matched by id AND by a loose title
 * contains check to catch variants like "Share…", "Search web", or locale
 * strings.
 */
internal fun selectionNoiseItemIds(items: List<SelectionMenuItem>): List<Int> {
    val knownNoiseIds = setOf(
        android.R.id.shareText,
        android.R.id.selectAll,
        // android.R.id.textAssist (= 0x1020041) is the slot the system
        // TextClassifier uses to inject "smart" suggestions like a
        // Google-branded "Define" or "Translate" chip. We have our own
        // Define / Sync to Audio actions, so strip whatever the
        // classifier picks here unconditionally. Without this strip a
        // "G Define" appears next to ours on the second-or-later
        // selection (after the async classifier pass finishes).
        android.R.id.textAssist,
        // Some OEMs use non-android-framework ids for these text-classifier
        // items; match by title below catches them.
    )
    // Substrings (case-insensitive) to match against the item title.
    // Use contains rather than exact match so we catch "Share…",
    // "Select all", "Search web", OEM-specific labels, etc.
    val noiseTitleSubstrings = listOf(
        "share", "select all", "translate",
        "web search", "search web", "assist",
    )
    val itemsToRemove = mutableListOf<Int>()
    for (item in items) {
        val itemId = item.id
        val titleLower = item.title.orEmpty().lowercase().trim().trimEnd('…', '.', ' ')
        // Don't touch our own custom items
        if (itemId == R.id.action_define || itemId == R.id.action_sync_selection) continue
        // Don't touch Copy — users still need it
        if (itemId == android.R.id.copy) continue
        // Leave Read Aloud / accessibility items alone
        if ("read aloud" in titleLower || "speak" in titleLower) continue
        val matchesId = itemId in knownNoiseIds
        val matchesTitle = noiseTitleSubstrings.any { it in titleLower }
        if (matchesId || matchesTitle) itemsToRemove += itemId
    }
    return itemsToRemove
}

/**
 * The word "Define" looks up: the first whitespace-separated token of the
 * selection with leading/trailing punctuation shorn off (apostrophes and
 * hyphens are part of a word). Empty when there is no such word.
 */
internal fun firstDefinableToken(selection: String): String =
    selection.trim().split(Regex("\\s+"))
        .firstOrNull()
        ?.trim { !it.isLetter() && it != '\'' && it != '-' }
        .orEmpty()

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
