package com.booksync.ui.reader

import android.content.Context
import android.graphics.Typeface
import android.text.SpannableString
import android.text.style.StyleSpan
import android.view.LayoutInflater
import android.view.View
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AlertDialog
import com.booksync.R
import com.booksync.data.remote.DictionaryEntry

/**
 * Inflates [R.layout.dialog_definition] and shows it as an AlertDialog.
 * Kept as a plain helper (no Fragment) to match the existing reader pattern
 * used by the font-settings / display dialog in [ReaderActivity.showFontSettings].
 */
object DictionarySheet {

    /** Number of definitions to show per meaning before we cut off. */
    private const val DEFINITIONS_PER_MEANING = 3

    fun show(context: Context, word: String, entries: List<DictionaryEntry>) {
        val view = LayoutInflater.from(context).inflate(R.layout.dialog_definition, null, false)

        val wordView = view.findViewById<TextView>(R.id.text_word)
        val phoneticView = view.findViewById<TextView>(R.id.text_phonetic)
        val container = view.findViewById<LinearLayout>(R.id.meanings_container)

        // Prefer the first entry's word/phonetic. Fall back to what the user selected.
        val primary = entries.firstOrNull()
        wordView.text = primary?.word ?: word
        val phonetic = entries.firstNotNullOfOrNull { it.phonetic?.takeIf(String::isNotBlank) }
        if (phonetic.isNullOrBlank()) {
            phoneticView.visibility = View.GONE
        } else {
            phoneticView.text = phonetic
        }

        // Flatten meanings across all entries — the API returns a list of entries
        // (same word, different etymologies) and each entry has its own meanings.
        val meanings = entries.flatMap { it.meanings }
        if (meanings.isEmpty()) {
            container.addView(bodyText(context, "No definitions found."))
        } else {
            meanings.forEach { meaning ->
                container.addView(partOfSpeechLabel(context, meaning.partOfSpeech))
                meaning.definitions.take(DEFINITIONS_PER_MEANING).forEach { def ->
                    container.addView(bodyText(context, "• ${def.definition}"))
                    def.example?.takeIf(String::isNotBlank)?.let { ex ->
                        container.addView(exampleText(context, "\u201C$ex\u201D"))
                    }
                }
                container.addView(spacer(context, 12))
            }
        }

        AlertDialog.Builder(context)
            .setView(view)
            .setPositiveButton("Close") { d, _ -> d.dismiss() }
            .show()
    }

    private fun partOfSpeechLabel(context: Context, pos: String): TextView {
        val tv = TextView(context)
        val styled = SpannableString(pos)
        styled.setSpan(StyleSpan(Typeface.ITALIC), 0, pos.length, 0)
        tv.text = styled
        tv.textSize = 13f
        tv.setTextColor(context.resolveThemeColor(android.R.attr.textColorTertiary))
        tv.setPadding(0, dp(context, 8), 0, dp(context, 4))
        return tv
    }

    private fun bodyText(context: Context, text: String): TextView {
        val tv = TextView(context)
        tv.text = text
        tv.textSize = 15f
        tv.setTextColor(context.resolveThemeColor(android.R.attr.textColorPrimary))
        tv.setPadding(0, dp(context, 2), 0, dp(context, 2))
        return tv
    }

    private fun exampleText(context: Context, text: String): TextView {
        val tv = TextView(context)
        tv.text = text
        tv.textSize = 13f
        tv.setTypeface(null, Typeface.ITALIC)
        tv.setTextColor(context.resolveThemeColor(android.R.attr.textColorSecondary))
        tv.setPadding(dp(context, 12), 0, 0, dp(context, 4))
        return tv
    }

    private fun spacer(context: Context, heightDp: Int): View {
        val v = View(context)
        v.layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            dp(context, heightDp),
        )
        return v
    }

    private fun dp(context: Context, value: Int): Int =
        (context.resources.displayMetrics.density * value).toInt()

    /**
     * Resolve a theme color attribute to a concrete ARGB int.
     *
     * NOTE: `theme.resolveAttribute(...).data` only returns a real color when
     * the attribute points at a literal color value. For things like
     * `?android:attr/textColorPrimary`, the attribute points at a
     * ColorStateList resource — `.data` is then the resource id, not an ARGB
     * int, which shows up as near-invisible text. Use obtainStyledAttributes,
     * which handles both cases for us.
     */
    private fun Context.resolveThemeColor(attr: Int): Int {
        val ta = theme.obtainStyledAttributes(intArrayOf(attr))
        return try {
            ta.getColor(0, 0xFFFFFFFF.toInt())
        } finally {
            ta.recycle()
        }
    }
}
