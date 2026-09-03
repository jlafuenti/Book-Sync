package com.booksync.data.util

import java.io.File

/**
 * The local filename to use for a server-supplied one, or null if it is not a
 * plain name (issue #177).
 *
 * `ebook.filename` / `audiobook.filename` arrive in the server's JSON and used
 * to be joined onto `filesDir` verbatim. A server the user typed by mistake, a
 * compromised server once it is internet-facing, or an attacker on a cleartext
 * LAN could answer with `"../datastore/booksync_prefs.preferences_pb"` and have
 * the app overwrite its own token store or Room database with an EPUB body;
 * DataStore then throws `CorruptionException` on the next start, a crash loop
 * until storage is cleared. The delete paths were worse — `.delete()` on the
 * joined path removes any file under `filesDir`.
 *
 * Returns null rather than throwing, so each caller decides: a download should
 * fail loudly, a cover lookup should quietly show no art.
 *
 * **This is an identity mapping for every real file.** The server only ever
 * emits `os.path.basename(...)`, so nothing on disk needs renaming and nothing
 * that works today should stop working. That also sets the risk: over-rejecting
 * a legitimate name would break a book that currently opens, which is why
 * unicode, spaces, brackets, dots and percent signs are all explicitly fine.
 *
 * Deliberately does **not** URL-decode. `"%2e%2e"` names a file literally called
 * `%2e%2e` — the filesystem will not decode it, so treating it as `..` would
 * invent an attack that does not exist while corrupting real filenames that
 * contain a percent sign.
 */
fun localFileName(serverFilename: String): String? {
    if (serverFilename.isBlank()) return null

    // A NUL truncates a C string in any native layer below the JVM, which keeps
    // the whole thing. Never legitimate in a filename.
    if (serverFilename.contains('\u0000')) return null

    // Any separator means this is a path, not a name — including a backslash,
    // which is a separator on the platform this code is compiled against even
    // though the server is POSIX.
    if (serverFilename.contains('/') || serverFilename.contains('\\')) return null

    // "." and ".." carry no separator but are not names either.
    if (serverFilename == "." || serverFilename == "..") return null

    // Belt and braces: after all of the above the basename must equal the input.
    // If it ever does not, the platform disagrees with the checks above and the
    // safe answer is to refuse.
    val base = File(serverFilename).name
    return base.takeIf { it == serverFilename && it.isNotBlank() }
}
