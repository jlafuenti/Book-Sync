package com.booksync.auto

import androidx.media3.common.MediaItem

/**
 * Whether Android Auto may serve anything at all (issue #573).
 *
 * Sign-out clears the tokens and the role and nothing else: the Room cache and
 * the downloaded audio files stay, deliberately, because that is what makes
 * signing back in cheap (`docs/android.md`). On the phone that is harmless —
 * every screen is behind the login screen. In the car it is not: the browse tree
 * is built from that cache, and a downloaded file needs no token to play. Signed
 * out, a head unit listed the previous account's whole library with artwork and
 * played their downloaded audiobook at their stored position.
 *
 * The fix is a gate rather than a cache wipe: the surfaces below stop serving,
 * the data stays. Everything in this file is pure so the decision is testable —
 * `AudioPlayerService`, which wires it to Media3, is excluded from Kover.
 *
 * **The gate is on the account, never on the size of a list.** The original bug
 * was precisely that: the signed-out message existed, but it was only reachable
 * as the empty-list fallback, so a populated cache hid it forever. A test that
 * pins the message is not a test of the gate — see `AutoAccountGateTest` and the
 * source guards in `AutoWiringTest`.
 */
fun autoHasAccount(accessToken: String?): Boolean = !accessToken.isNullOrBlank()

/**
 * Why a play request was refused, carried on the exception the Media3 callbacks
 * hand back.
 *
 * Android Auto draws its own generic "Source error" card for any playback
 * failure, so this is not guaranteed to reach the windscreen — but it is what
 * the diagnostics log and any host that does surface a reason will show, and it
 * is the difference between "you are signed out" and "you are offline and this
 * book is not downloaded", which looked identical to the driver before.
 * Deliberately a separate string from [AUTO_SIGNED_OUT_MESSAGE]: that one is a
 * browse row telling you where to go, this one is a reason a tap did nothing.
 */
const val AUTO_SIGNED_OUT_PLAYBACK_MESSAGE =
    "Signed out — open Tandem on your phone to sign in"

/**
 * The whole browse tree when there is no account: one leaf saying where to fix
 * it, browsable by nothing and playable by nothing.
 *
 * A single row rather than a blank list because a blank list in a car tells the
 * driver nothing, and rather than an error because there is no error — the app
 * is working, it just has no one signed in.
 */
fun autoSignedOutNode(): List<MediaItem> = listOf(autoMessageItem(AUTO_SIGNED_OUT_MESSAGE))

/**
 * One browse node, gated.
 *
 * [load] is **not called** when there is no account, so the cache is not read at
 * all rather than read and discarded. That matters for more than tidiness: the
 * loaders fetch cover art over the network, and a signed-out browse must not
 * turn into a burst of requests carrying no token.
 */
suspend fun autoGatedBrowse(
    hasAccount: Boolean,
    load: suspend () -> List<MediaItem>,
): List<MediaItem> = if (hasAccount) load() else autoSignedOutNode()

/**
 * Voice search, gated. Signed out returns **nothing**, not a message row: a
 * browser draws its own "no results", and a row among results reads as a hit —
 * Assistant would try to play it.
 */
suspend fun <T> autoGatedSearch(
    hasAccount: Boolean,
    load: suspend () -> List<T>,
): List<T> = if (hasAccount) load() else emptyList()

/**
 * A play-or-resolve request, gated. Null is the refusal, and the caller turns it
 * into whatever its Media3 callback needs — a failed future or an error result.
 *
 * This covers the path the issue's downloaded book took: nothing below this
 * point can refuse a file that is already on disk, because playing it needs no
 * token and asks the server nothing.
 */
suspend fun <T : Any> autoGatedPlayback(
    hasAccount: Boolean,
    resolve: suspend () -> T?,
): T? = if (hasAccount) resolve() else null
