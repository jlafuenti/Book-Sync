package com.booksync.ui.reader

import android.content.Context

/**
 * The reader's "turn pages by tapping the edges" preference (issue #585),
 * lifted out as its own tiny class the same way [ReaderDisplaySettings]
 * holds font/theme/spacing — a separate class rather than a field on
 * [ReaderDisplaySettings] because this isn't an `EpubPreferences` value
 * pushed to the navigator; it only gates [decideReaderTapAction] in
 * [ReaderActivity].
 *
 * Persisted in the same `reader_display` SharedPreferences file
 * [ReaderDisplaySettings] already uses — both are "how this reader behaves"
 * settings for the same book-agnostic scope (per device, not per book) — so
 * a saved preference from before this class existed is read back exactly as
 * it was written, and there's no second prefs file to migrate later.
 *
 * Defaults on: most readers benefit from edge taps, and the middle 56% of
 * the page (see [READER_TAP_EDGE_FRACTION]) is left untouched for anyone who
 * taps there often, so there is little reason to make people find a setting
 * before they get the feature.
 */
class ReaderEdgeTapSettings(private val context: Context) {

    private companion object {
        const val PREFS_NAME = "reader_display"
        const val KEY_ENABLED = "edge_tap_enabled"
    }

    /** Whether an edge tap should turn the page. Call [load] once before reading this. */
    var enabled: Boolean = true
        private set

    /** Read the saved preference into [enabled]. Call once, before the navigator exists. */
    fun load() {
        enabled = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .getBoolean(KEY_ENABLED, true)
    }

    fun setEnabled(value: Boolean) {
        enabled = value
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE).edit()
            .putBoolean(KEY_ENABLED, value)
            .apply()
    }
}
