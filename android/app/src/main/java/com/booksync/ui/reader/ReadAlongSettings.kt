package com.booksync.ui.reader

import android.content.Context
import android.content.SharedPreferences

/** How read-along marks the current sentence (issue #762). */
enum class ReadAlongStyle(val key: String) {
    HIGHLIGHT("highlight"), UNDERLINE("underline");

    companion object {
        fun fromKey(key: String?): ReadAlongStyle = entries.firstOrNull { it.key == key } ?: HIGHLIGHT
    }
}

/**
 * The read-along mark preference, device-wide like every other reader
 * setting, in the same `reader_display` file ([ReaderEdgeTapSettings] has
 * the rationale). Default highlight: the tester asked for either; a
 * highlight is the more visible of the two on a first run.
 */
class ReadAlongSettings(private val prefs: SharedPreferences) {
    constructor(context: Context) :
        this(context.getSharedPreferences(ReaderDisplaySettings.PREFS_NAME, Context.MODE_PRIVATE))

    private companion object {
        const val KEY_STYLE = "read_along_style"

        /** Amber 500; Readium's highlight template multiplies the tint over the page, so it stays readable. */
        const val HIGHLIGHT_TINT = 0x66FFC107
        const val UNDERLINE_TINT = 0xFFFFC107.toInt()
    }

    private var listener: ((ReadAlongStyle) -> Unit)? = null

    var style: ReadAlongStyle
        get() = ReadAlongStyle.fromKey(prefs.getString(KEY_STYLE, null))
        set(value) {
            prefs.edit().putString(KEY_STYLE, value.key).apply()
            listener?.invoke(value)
        }

    /** ARGB tint for the current style. Amber for both; the underline is opaque, the highlight translucent. */
    val tint: Int get() = if (style == ReadAlongStyle.UNDERLINE) UNDERLINE_TINT else HIGHLIGHT_TINT

    fun setListener(listener: ((ReadAlongStyle) -> Unit)?) {
        this.listener = listener
    }
}
