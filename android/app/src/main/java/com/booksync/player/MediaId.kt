package com.booksync.player

import android.util.Log

/**
 * The single owner of the Media3 media-id wire format (issue #141).
 *
 * Exactly two id shapes exist — "pair_N" for paired books and "audiobook_N"
 * for standalone audiobooks — and seven dispatchers key on them (completion,
 * Cast item building, position saves, resumption, Auto's resolveMediaItem,
 * the mini-player). The phone player once invented a third shape
 * ("standalone_N") that no dispatcher recognised, so every service-side
 * position save silently no-oped for standalone playback. All building and
 * parsing goes through this type so that cannot happen silently again.
 *
 * The wire format must not change: media ids are persisted in SharedPreferences
 * (PREF_LAST_MEDIA_ID, for Cast resumption) and used by Android Auto's browse
 * tree.
 */
sealed class MediaId {
    abstract val value: String

    data class Pair(val pairId: Int) : MediaId() {
        override val value: String get() = "pair_$pairId"
    }

    data class Audiobook(val audiobookId: Int) : MediaId() {
        override val value: String get() = "audiobook_$audiobookId"
    }

    companion object {
        private const val TAG = "MediaId"

        /** Returns null — with a warning, so it shows up in diagnostics rather
         *  than vanishing — for any id that is not one of the two known shapes. */
        fun parse(raw: String): MediaId? {
            val parsed = when {
                raw.startsWith("pair_") ->
                    raw.removePrefix("pair_").toIntOrNull()?.let { Pair(it) }
                raw.startsWith("audiobook_") ->
                    raw.removePrefix("audiobook_").toIntOrNull()?.let { Audiobook(it) }
                else -> null
            }
            if (parsed == null && raw.isNotEmpty()) {
                Log.w(TAG, "Unknown media id '$raw' — no dispatcher will handle it")
            }
            return parsed
        }
    }
}
