package com.booksync.auto

import android.net.Uri
import android.os.Bundle
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata

/** Browse-tree node ids. The car sends these back verbatim, so they are wire format. */
const val AUTO_ROOT_ID = "[root]"
const val AUTO_TAB_CONTINUE = "continue_listening"
const val AUTO_TAB_LIBRARY = "library"

/**
 * The id of the "there is nothing here, and here is why" leaf. Deliberately not
 * a `pair_`/`audiobook_` id: nothing must ever try to play it.
 */
const val AUTO_MESSAGE_ID = "message"

const val AUTO_SIGNED_OUT_MESSAGE = "Open Tandem on your phone to sign in"
const val AUTO_EMPTY_LIBRARY_MESSAGE = "No books yet — add them in Tandem on your phone"
const val AUTO_NOTHING_STARTED_MESSAGE = "Nothing started yet — pick a book from Library"

/** Shown when reading the local library itself failed — not merely empty. */
const val AUTO_UNAVAILABLE_MESSAGE = "Tandem couldn't load your library — open it on your phone"

/**
 * How many rows one browse node may hold.
 *
 * The car app quality review cares that a level is bounded and loads fast; a
 * driver is not scrolling past a hundred rows at 60 mph either way. The
 * alphabetical Library is the node that can realistically grow past this.
 */
const val AUTO_MAX_ITEMS_PER_NODE = 100

/**
 * One row of the browse tree, before it becomes a Media3 [MediaItem].
 *
 * Pairs and standalone audiobooks are different Room rows with different column
 * names; this is the shape they both flatten to, so the ordering, de-duplication
 * and capping below can be tested without a database or an Android runtime.
 */
data class AutoBook(
    /** `pair_N` or `audiobook_N` — see `com.booksync.player.MediaId`. */
    val mediaId: String,
    val title: String,
    val author: String? = null,
    val series: String? = null,
    val audiobookId: Int,
    /** Non-null when this row is a paired book. */
    val pairId: Int? = null,
    val resumePositionMs: Long = 0L,
    val durationMs: Long = 0L,
    /**
     * What the URL and artwork resolvers need to look the file up — the
     * server's filename for the audio, and `audiobooks.cover_path`. Carried
     * here rather than looked up again so one browse is one pass over Room.
     */
    val audioFilename: String? = null,
    val serverCoverPath: String? = null,
)

fun AutoBook.asSearchable() = AutoSearchable(mediaId, title, author, series)

/**
 * The root's children. Two tabs, both folders — this is what fixes the browse
 * depth at two levels (root → tab → book), which the car checklist bounds.
 */
fun autoRootTabs(): List<MediaItem> = listOf(
    browseFolder(AUTO_TAB_CONTINUE, "Continue Listening"),
    browseFolder(AUTO_TAB_LIBRARY, "Library"),
)

/**
 * Continue Listening: the recency order the repository already produced, pairs
 * first, capped. No re-sorting — the ordering is the point of the tab, and it
 * comes from `updatedAt` on the bookmark/progress row.
 */
fun continueListeningBooks(pairs: List<AutoBook>, standalone: List<AutoBook>): List<AutoBook> =
    (pairs + standalone).take(AUTO_MAX_ITEMS_PER_NODE)

/**
 * Library: everything, alphabetical, with an audiobook dropped when a pair
 * already represents it — otherwise a paired book appears twice, once under
 * each id, and the two rows resume at different positions.
 */
fun libraryBooks(pairs: List<AutoBook>, standalone: List<AutoBook>): List<AutoBook> =
    mergedLibrary(pairs, standalone).take(AUTO_MAX_ITEMS_PER_NODE)

/**
 * The same de-duplicated, alphabetical library, **uncapped**.
 *
 * Voice search indexes this rather than [libraryBooks]: a node cap is a
 * rendering limit, and applying it to the index would make the 101st book
 * alphabetically unsayable.
 */
fun mergedLibrary(pairs: List<AutoBook>, standalone: List<AutoBook>): List<AutoBook> {
    val pairedAudiobookIds = pairs.map { it.audiobookId }.toSet()
    return (pairs + standalone.filterNot { it.audiobookId in pairedAudiobookIds })
        .sortedBy { it.title.lowercase() }
}

/**
 * Turn browse rows into Media3 items, or into a single message leaf when there
 * are none.
 *
 * An empty *browse node* is a car-checklist failure: the driver gets a blank
 * list with no way to tell "you are signed out" from "this tab is genuinely
 * empty". So a browsing caller supplies the sentence to show instead. Search
 * results pass null — a browser renders its own "no results", and a message
 * row there would look like a hit.
 *
 * [urlFor] returns the playback source — the downloaded file, or the server
 * stream since issue #171 — as a string rather than a [Uri] so this stays a
 * pure decision (and so unit tests, where `Uri.parse` is a stub, can hold it).
 * A row with no source at all is dropped rather than shown as a dead button.
 */
fun autoBrowseItems(
    books: List<AutoBook>,
    emptyMessage: String?,
    urlFor: (AutoBook) -> String?,
    artworkFor: (AutoBook) -> Uri? = { null },
): List<MediaItem> {
    val items = books.mapNotNull { book ->
        val url = urlFor(book) ?: return@mapNotNull null
        autoBookItem(book, url, artworkFor(book))
    }
    if (items.isNotEmpty() || emptyMessage == null) return items
    return listOf(autoMessageItem(emptyMessage))
}

/** A non-playable, non-browsable row that exists only to say something. */
fun autoMessageItem(message: String): MediaItem = MediaItem.Builder()
    .setMediaId(AUTO_MESSAGE_ID)
    .setMediaMetadata(
        MediaMetadata.Builder()
            .setTitle(message)
            .setIsBrowsable(false)
            .setIsPlayable(false)
            .build()
    )
    .build()

private fun browseFolder(id: String, title: String): MediaItem = MediaItem.Builder()
    .setMediaId(id)
    .setMediaMetadata(
        MediaMetadata.Builder()
            .setTitle(title)
            .setIsBrowsable(true)
            .setIsPlayable(false)
            .setMediaType(MediaMetadata.MEDIA_TYPE_FOLDER_MIXED)
            .build()
    )
    .build()

/**
 * One playable book row. Shared by the browse tree and by the service's
 * resolve path (`onGetItem` / `onSetMediaItems`, issue #225), so a book cannot
 * carry one resume position in the list and another when Auto asks for it by
 * id. [url] is the playback source — download or stream — already chosen.
 */
fun autoBookItem(book: AutoBook, url: String, artwork: Uri?): MediaItem {
    val extras = Bundle().apply {
        putLong("resumePositionMs", book.resumePositionMs)
        putLong("durationMs", book.durationMs)
        putString("sourceType", if (book.pairId != null) "pair" else "standalone")
        book.pairId?.let { putInt("pairId", it) }
        putInt("audiobookId", book.audiobookId)
    }
    return MediaItem.Builder()
        .setMediaId(book.mediaId)
        .setUri(url)
        .setMediaMetadata(
            MediaMetadata.Builder()
                .setTitle(book.title)
                .setArtist(book.author)
                .setArtworkUri(artwork)
                .setMediaType(MediaMetadata.MEDIA_TYPE_AUDIO_BOOK)
                .setIsBrowsable(false)
                .setIsPlayable(true)
                .setExtras(extras)
                .build()
        )
        .build()
}
