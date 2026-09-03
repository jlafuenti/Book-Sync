package com.booksync.data.remote

/**
 * The client half of the API-version handshake (issue #174).
 *
 * Pure, and free of Android and DI types so it is unit-testable — same pattern
 * as `ServerUrlPolicy`. Everything that needs a network call or a scope lives in
 * [ServerVersionGate].
 */

/**
 * The API version this build was written against; compared with the server's
 * `api_version` from `GET /api/health`.
 *
 * **Bump this only when the app starts requiring server behaviour that an older
 * server does not have** — and in the same change that starts requiring it. It
 * is the app's claim about what it needs, not a release counter: raising it
 * without a real requirement tells every operator running a perfectly good
 * server to go upgrade it.
 *
 * The server's own copy is `server/version.py`; `docs/android.md` says when the
 * two move.
 */
const val SUPPORTED_API_VERSION = 1

/**
 * Which warning to show, or none.
 *
 * Deliberately not a string: the copy lives in `res/values/strings.xml` and the
 * choice lives here, so the decision is testable without a Context and the two
 * banners cannot be swapped by accident. They give opposite instructions —
 * "update the app" and "upgrade the server" — and the wrong one sends someone
 * off to make a change that cannot help.
 */
enum class VersionBanner { SERVER_NEWER, SERVER_OLDER }

object VersionCompat {

    /** Which side, if either, is behind. */
    enum class Verdict {
        /** Same API version. */
        Ok,

        /** The server speaks a newer API than this build: update the app. */
        ServerNewer,

        /** The server speaks an older API than this build: upgrade the server. */
        ServerOlder,

        /**
         * The server did not say. Every deployment predating issue #174 answers
         * `/api/health` with `{"status": "healthy"}` and nothing else, and an
         * unreachable server says nothing at all — neither is a mismatch, and
         * warning about them would put a banner in front of users with no
         * problem to fix until the banner stops being read.
         */
        Unknown,
    }

    /**
     * Compare the server's advertised API version with this build's.
     *
     * [clientApi] is a parameter rather than a direct read of
     * [SUPPORTED_API_VERSION] so the four verdicts can be exercised without
     * changing the constant — which is a shipping promise, not a test fixture.
     */
    fun compare(serverApi: Int?, clientApi: Int = SUPPORTED_API_VERSION): Verdict = when {
        serverApi == null -> Verdict.Unknown
        serverApi > clientApi -> Verdict.ServerNewer
        serverApi < clientApi -> Verdict.ServerOlder
        else -> Verdict.Ok
    }

    /** The banner a verdict earns, if any. Agreement and silence earn none. */
    fun bannerFor(verdict: Verdict): VersionBanner? = when (verdict) {
        Verdict.ServerNewer -> VersionBanner.SERVER_NEWER
        Verdict.ServerOlder -> VersionBanner.SERVER_OLDER
        Verdict.Ok, Verdict.Unknown -> null
    }
}
