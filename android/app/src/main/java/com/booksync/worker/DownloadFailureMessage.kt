package com.booksync.worker

import java.io.IOException

/**
 * Why a download never started, in words worth showing someone (issue #338).
 *
 * The old answer was `Result.failure("Audiobook not found in DB")`: accurate
 * about the cache, useless to the user, and never rendered anywhere anyway. A
 * download can fail to start for three quite different reasons, and telling
 * them apart is the whole point — "the server doesn't have this book" is final,
 * "we couldn't reach the server" will fix itself, and an expired session needs
 * an action only the user can take.
 *
 * @param title the book as the user named it
 * @param cause what went wrong, or null when the server answered and simply
 *   does not have the book
 */
fun downloadUnavailableMessage(title: String, cause: Throwable?): String {
    if (cause == null) return "\"$title\" is no longer in the library on the server."

    // Retrofit and okio wrap the real cause; matching only the top-level type
    // would report a genuine network blip as a missing book.
    val causes = generateSequence(cause) { it.cause }.take(8).toList()
    if (causes.any { it is IOException }) {
        return "Can't reach the server — \"$title\" was not downloaded."
    }

    // The repository reports HTTP failures as Exception("HTTP <code>: …") and
    // Retrofit's own HttpException reads "HTTP <code> <reason>", so the status
    // comes out of the message either way — same approach as
    // [classifyDownloadFailure].
    val status = causes.firstNotNullOfOrNull { c ->
        Regex("""HTTP (\d{3})""").find(c.message.orEmpty())?.groupValues?.get(1)?.toIntOrNull()
    }
    return when (status) {
        401 -> "Your session has expired. Sign in again."
        403 -> "You don't have permission to download \"$title\"."
        404 -> "\"$title\" is no longer in the library on the server."
        else -> "Couldn't download \"$title\": ${cause.message ?: "unknown error"}"
    }
}
