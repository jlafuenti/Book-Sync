package com.booksync.ui.tour

import android.content.SharedPreferences
import com.booksync.ui.reader.ReaderProgressPrefs

/**
 * The seam [TourController] uses to record and restore the reader's
 * tap-to-cycle progress mode ("percent" / "pages" / "chapter" / "time")
 * around a walkthrough run (issue #743) — the `reader_progress` step invites
 * the user to tap it, and the tour leaves no trace, the same as it already
 * does for the pair it opens ([TourNav.CleanUp]).
 *
 * Wraps the same `reader_display` `SharedPreferences` file
 * [ReaderProgressPrefs] already persists the mode in — that class is built
 * from a `Context` on demand inside
 * [com.booksync.ui.reader.ReaderActivity] rather than injected, so this is
 * the one place outside the reader that needs to read or write it. Takes the
 * `SharedPreferences` instance directly (the same pattern [ReaderProgressPrefs]
 * itself uses) so JVM tests can hand it a fake rather than needing
 * Robolectric.
 */
class ReaderProgressModeStore(sharedPreferences: SharedPreferences) {
    private val prefs = ReaderProgressPrefs(sharedPreferences)

    fun get(): String = prefs.progressMode

    fun set(mode: String) {
        prefs.progressMode = mode
    }
}
