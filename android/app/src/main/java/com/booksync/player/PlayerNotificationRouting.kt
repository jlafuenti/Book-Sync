package com.booksync.player

import com.booksync.ui.Routes

/**
 * Intent action for a tap on the playback notification's cover art or body
 * (issue #684). Distinguishes that launch from an ordinary one so
 * `MainActivity` knows to hand [EXTRA_OPEN_PLAYER_MEDIA_ID] off to
 * [PendingPlayerNavigation] rather than treat it as a plain cold start.
 */
const val ACTION_OPEN_PLAYER = "com.booksync.action.OPEN_PLAYER"

/** Extra carrying the media id whose player screen [ACTION_OPEN_PLAYER] should open. */
const val EXTRA_OPEN_PLAYER_MEDIA_ID = "com.booksync.extra.OPEN_PLAYER_MEDIA_ID"

/**
 * Where a tap on the playback notification should take the app (issue #684):
 * the player screen for whatever [mediaId] names, resolved through the same
 * [MediaId] wire format every other dispatcher uses rather than a
 * home-grown parser of its own (that shortcut is how issue #141 happened).
 *
 * Returns null for anything [MediaId.parse] can't make sense of — a null or
 * empty id, or an unknown shape — and the caller opens the app normally
 * instead of navigating nowhere.
 */
fun openPlayerRouteFor(mediaId: String?): String? =
    when (val id = mediaId?.let { MediaId.parse(it) }) {
        is MediaId.Pair -> Routes.player(id.pairId)
        is MediaId.Audiobook -> Routes.playerStandalone(id.audiobookId)
        null -> null
    }
