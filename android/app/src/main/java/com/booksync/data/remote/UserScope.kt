package com.booksync.data.remote

/**
 * Which account a cached row belongs to (issue #314).
 *
 * **Server *and* user, not user alone.** User ids are per-server: point the app at
 * a different Tandem and its user 2 is a different person from this one's user 2.
 * Keying on the id alone would let one server's rows be read — and its queued
 * writes replayed — as the other server's user of the same number.
 *
 * A single opaque string rather than two columns: it goes into three primary keys,
 * and one column keeps those keys and every query that carries the scope simple.
 * The format is an implementation detail; nothing parses it back apart.
 *
 * [LEGACY] marks rows written before the cache was scoped at all. They are adopted
 * once, by the first scope to authenticate after the upgrade — on the overwhelmingly
 * common single-account device that is simply correct, and it is the only way to
 * avoid discarding reading positions the server has never seen.
 */
@JvmInline
value class UserScope(val key: String) {

    companion object {
        /** Rows from before scoping existed. Adopted once; never written anew. */
        val LEGACY = UserScope("")

        /**
         * The scope for a signed-in session, or null if either half is unknown —
         * an unreadable token, or no server configured. Null means "do not guess":
         * callers hold writes rather than attribute them to the wrong account.
         */
        fun of(serverUrl: String?, userId: Int?): UserScope? {
            if (userId == null) return null
            val server = serverUrl?.trim()?.trimEnd('/').orEmpty()
            if (server.isEmpty()) return null
            return UserScope("$server|$userId")
        }
    }
}
