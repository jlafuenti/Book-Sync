package com.booksync.ui.reader

/**
 * Whether opening this pair should fetch the EPUB by itself (issue #171).
 *
 * The reader used to stop at a prompt for a file that is typically a couple of
 * megabytes, on a screen the user reached by pressing "Read". Streaming an EPUB
 * through Readium is not worth building; downloading it on open is.
 *
 * [alreadyRequested] is the whole reason this is a function rather than an `if`
 * in the collector: `ReaderViewModel` learns about the pair from
 * `getPairsFlow()`, which re-emits on every library refresh, so an unguarded
 * auto-download would enqueue again on each emission. It also stays set after a
 * cancel — the user said no, and the visible download button is how they take
 * it back.
 */
fun shouldAutoDownloadEbook(ebookDownloaded: Boolean, alreadyRequested: Boolean): Boolean =
    !ebookDownloaded && !alreadyRequested
