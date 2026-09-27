package com.booksync.ui.reader

import android.content.SharedPreferences
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json

/**
 * Per-device preferences for the tap-to-cycle reader progress indicator
 * (issue #730): which [ReaderProgress] progress mode and page mode are
 * showing, the rolling reading-speed samples behind the "time left in
 * chapter" estimate, and the print page count fetched from the server for
 * each ebook.
 *
 * Persisted in the same `reader_display` `SharedPreferences` file
 * [ReaderDisplaySettings] and [ReaderEdgeTapSettings] already use — one more
 * "how this reader behaves" setting for the same book-agnostic, per-device
 * scope, so there is no new prefs file to migrate.
 *
 * Takes the `SharedPreferences` instance directly (rather than a `Context`)
 * so tests can hand it an in-memory fake; [ReaderActivity] builds it with
 * `getSharedPreferences(ReaderDisplaySettings.PREFS_NAME, MODE_PRIVATE)`.
 */
class ReaderProgressPrefs(private val prefs: SharedPreferences) {

    private companion object {
        const val KEY_PROGRESS_MODE = "progress_mode"
        const val KEY_PAGE_MODE = "page_mode"
        const val KEY_SPEED_SAMPLES = "speed_samples"
        const val KEY_PRINT_PAGES_PREFIX = "print_pages_"
    }

    /** One of [ReaderProgress.PROGRESS_MODES]; an unrecognized stored value reads as `"percent"`. */
    var progressMode: String
        get() = ReaderProgress.parseProgressMode(prefs.getString(KEY_PROGRESS_MODE, null))
        set(value) {
            prefs.edit().putString(KEY_PROGRESS_MODE, value).apply()
        }

    /** `"print"` or `"ebook"`; anything else (including unset) reads as `"ebook"`. */
    var pageMode: String
        get() = ReaderProgress.parsePageMode(prefs.getString(KEY_PAGE_MODE, null))
        set(value) {
            prefs.edit().putString(KEY_PAGE_MODE, value).apply()
        }

    /**
     * Rolling reading-speed samples ([ReaderProgress.addSpeedSample]), stored
     * as a JSON array of doubles. Unset or corrupt reads back as an empty
     * list rather than throwing — a bad value here should just reset the
     * speed estimate to its default, not crash the reader.
     */
    var speedSamples: List<Double>
        get() {
            val raw = prefs.getString(KEY_SPEED_SAMPLES, null) ?: return emptyList()
            return runCatching { Json.decodeFromString<List<Double>>(raw) }.getOrDefault(emptyList())
        }
        set(value) {
            prefs.edit().putString(KEY_SPEED_SAMPLES, Json.encodeToString(value)).apply()
        }

    /** The last print page count fetched from the server for this ebook, or null if never set. */
    fun printPageCount(ebookId: Int): Int? {
        val key = KEY_PRINT_PAGES_PREFIX + ebookId
        return if (prefs.contains(key)) prefs.getInt(key, 0) else null
    }

    /** `count == null` removes the stored key rather than writing a 0, so [printPageCount] reads back null. */
    fun setPrintPageCount(ebookId: Int, count: Int?) {
        val key = KEY_PRINT_PAGES_PREFIX + ebookId
        val editor = prefs.edit()
        if (count == null) editor.remove(key) else editor.putInt(key, count)
        editor.apply()
    }
}
