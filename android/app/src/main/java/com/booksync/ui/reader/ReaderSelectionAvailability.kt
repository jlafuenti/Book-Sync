package com.booksync.ui.reader

import com.booksync.data.local.entity.BookPairEntity

/**
 * Whether the reader's selection-toolbar "Sync to Audio" action has anything
 * to resolve against (issue #597 Track C, "Selection sync while streaming").
 *
 * Before this, [ReaderSelectionController.Host.syncToAudioAvailable] required
 * a *downloaded* audiobook (`pair?.audiobookDownloaded == true`), which meant
 * the walkthrough's sentence-sync step could never run against the demo
 * server's streamed pairs. `syncSelectionToAudio` (`ReaderSelection.kt`) and
 * [PageAudioHandoff] never touch a local audio file — they resolve the match
 * through the cached sync map (`BookSyncRepository.epubToAudioText`) and hand
 * the result to the player, which streams — so the only real precondition is
 * a synced pair whose audio is reachable, either already on the device or
 * over the network right now.
 */
fun selectionSyncAvailable(pair: BookPairEntity?, isOnline: Boolean): Boolean =
    pair != null && pair.status == "synced" && (pair.audiobookDownloaded || isOnline)
