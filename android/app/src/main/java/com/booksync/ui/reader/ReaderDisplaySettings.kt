package com.booksync.ui.reader

import android.app.Activity
import android.content.Context
import android.view.View
import android.widget.TextView
import com.booksync.R
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import org.readium.r2.navigator.epub.EpubNavigatorFragment
import org.readium.r2.navigator.epub.EpubPreferences
import org.readium.r2.navigator.preferences.FontFamily
import org.readium.r2.navigator.preferences.Theme

/**
 * The reader's font / theme / spacing preferences and the dialog that edits
 * them (issue #227) — moved out of `ReaderActivity` unchanged. Persisted in
 * the `reader_display` SharedPreferences file, so a saved preference from
 * before the move is read back exactly as it was written.
 *
 * UI only: nothing here decides anything about positions.
 */
class ReaderDisplaySettings(private val context: Context) {

    private companion object {
        const val PREFS_NAME = "reader_display"
        const val KEY_FONT_SIZE = "font_size"
        const val KEY_THEME = "theme"
        const val KEY_FONT_FAMILY = "font_family"
        const val KEY_LINE_SPACING = "line_spacing"
        const val KEY_MARGINS = "margins"
    }

    /** Cumulative preferences, so changes don't wipe each other. */
    var preferences: EpubPreferences = EpubPreferences()
        private set

    /** Read the saved preferences into [preferences]. Call once, before the navigator exists. */
    fun load() {
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        val fontSize = prefs.getFloat(KEY_FONT_SIZE, 1.0f).toDouble()
        val themeName = prefs.getString(KEY_THEME, null)
        val theme = when (themeName) {
            "light" -> Theme.LIGHT
            "sepia" -> Theme.SEPIA
            "dark" -> Theme.DARK
            else -> null
        }
        val fontFamilyName = prefs.getString(KEY_FONT_FAMILY, null)
        val fontFamily = when (fontFamilyName) {
            "serif" -> FontFamily.SERIF
            "sans-serif" -> FontFamily.SANS_SERIF
            "cursive" -> FontFamily.CURSIVE
            "monospace" -> FontFamily.MONOSPACE
            "system" -> null // Default
            else -> null
        }
        val lineSpacingRaw = prefs.getFloat(KEY_LINE_SPACING, -1f)
        val lineSpacing = if (lineSpacingRaw > 0) lineSpacingRaw.toDouble() else null
        
        val marginsRaw = prefs.getFloat(KEY_MARGINS, -1f)
        val margins = if (marginsRaw > 0) marginsRaw.toDouble() else null

        preferences = EpubPreferences(
            fontSize = fontSize,
            theme = theme,
            fontFamily = fontFamily,
            lineHeight = lineSpacing,
            pageMargins = margins,
            publisherStyles = false
        )
    }

    private fun save() {
        val editor = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE).edit()
        editor.putFloat(KEY_FONT_SIZE, (preferences.fontSize ?: 1.0).toFloat())
        
        val themeName = when (preferences.theme) {
            Theme.LIGHT -> "light"
            Theme.SEPIA -> "sepia"
            Theme.DARK -> "dark"
            else -> null
        }
        if (themeName != null) editor.putString(KEY_THEME, themeName)
        else editor.remove(KEY_THEME)

        val fontFamilyName = when (preferences.fontFamily) {
            FontFamily.SERIF -> "serif"
            FontFamily.SANS_SERIF -> "sans-serif"
            FontFamily.CURSIVE -> "cursive"
            FontFamily.MONOSPACE -> "monospace"
            else -> null
        }
        if (fontFamilyName != null) editor.putString(KEY_FONT_FAMILY, fontFamilyName)
        else editor.remove(KEY_FONT_FAMILY)

        if (preferences.lineHeight != null) {
            editor.putFloat(KEY_LINE_SPACING, preferences.lineHeight!!.toFloat())
        } else {
            editor.remove(KEY_LINE_SPACING)
        }

        if (preferences.pageMargins != null) {
            editor.putFloat(KEY_MARGINS, preferences.pageMargins!!.toFloat())
        } else {
            editor.remove(KEY_MARGINS)
        }

        editor.apply()
    }

    /** Push the current preferences to a freshly created navigator. */
    fun apply(navigator: EpubNavigatorFragment) {
        navigator.submitPreferences(preferences)
    }

    /** The two-tab Text / Display dialog. Every change applies to [nav] immediately and is saved. */
    fun showDialog(activity: Activity, nav: EpubNavigatorFragment) {
        val dialogView = activity.layoutInflater.inflate(R.layout.dialog_display_settings, null)

        // Tab switching
        val tabLayout = dialogView.findViewById<com.google.android.material.tabs.TabLayout>(R.id.tab_layout)
        val textContent = dialogView.findViewById<View>(R.id.tab_text_content)
        val displayContent = dialogView.findViewById<View>(R.id.tab_display_content)

        tabLayout.addOnTabSelectedListener(object : com.google.android.material.tabs.TabLayout.OnTabSelectedListener {
            override fun onTabSelected(tab: com.google.android.material.tabs.TabLayout.Tab?) {
                when (tab?.position) {
                    0 -> {
                        textContent.visibility = View.VISIBLE
                        displayContent.visibility = View.GONE
                    }
                    1 -> {
                        textContent.visibility = View.GONE
                        displayContent.visibility = View.VISIBLE
                    }
                }
            }
            override fun onTabUnselected(tab: com.google.android.material.tabs.TabLayout.Tab?) {}
            override fun onTabReselected(tab: com.google.android.material.tabs.TabLayout.Tab?) {}
        })

        // ======== TEXT TAB ========

        // Font Family toggle group
        val fontGroup = dialogView.findViewById<com.google.android.material.button.MaterialButtonToggleGroup>(R.id.font_family_group)
        val btnFontSystem = dialogView.findViewById<com.google.android.material.button.MaterialButton>(R.id.btn_font_system)
        val btnFontSerif = dialogView.findViewById<com.google.android.material.button.MaterialButton>(R.id.btn_font_serif)
        val btnFontSans = dialogView.findViewById<com.google.android.material.button.MaterialButton>(R.id.btn_font_sans)

        // Pre-select current font
        when (preferences.fontFamily) {
            FontFamily.SERIF -> fontGroup.check(R.id.btn_font_serif)
            FontFamily.SANS_SERIF -> fontGroup.check(R.id.btn_font_sans)
            else -> fontGroup.check(R.id.btn_font_system)
        }

        fontGroup.addOnButtonCheckedListener { _, checkedId, isChecked ->
            if (isChecked) {
                val fontFamily = when (checkedId) {
                    R.id.btn_font_serif -> FontFamily.SERIF
                    R.id.btn_font_sans -> FontFamily.SANS_SERIF
                    else -> null
                }
                preferences = preferences.copy(fontFamily = fontFamily)
                nav.submitPreferences(preferences)
                save()
            }
        }

        // Font Size
        val btnFontDecrease = dialogView.findViewById<View>(R.id.btn_font_decrease)
        val btnFontIncrease = dialogView.findViewById<View>(R.id.btn_font_increase)
        val btnFontReset = dialogView.findViewById<View>(R.id.btn_font_reset)
        val textFontSize = dialogView.findViewById<TextView>(R.id.text_font_size)

        fun updateFontSize(newSize: Double?) {
            preferences = preferences.copy(fontSize = newSize)
            textFontSize.text = if (newSize != null) "${(newSize * 100).toInt()}%" else "100%"
            nav.submitPreferences(preferences)
            save()
        }
        textFontSize.text = "${((preferences.fontSize ?: 1.0) * 100).toInt()}%"

        btnFontDecrease.setOnClickListener {
            val current = preferences.fontSize ?: 1.0
            updateFontSize((current - 0.1).coerceAtLeast(0.5))
        }
        btnFontIncrease.setOnClickListener {
            val current = preferences.fontSize ?: 1.0
            updateFontSize((current + 0.1).coerceAtMost(3.0))
        }
        btnFontReset.setOnClickListener { updateFontSize(null) }

        // Line Spacing
        val btnSpacingDecrease = dialogView.findViewById<View>(R.id.btn_spacing_decrease)
        val btnSpacingIncrease = dialogView.findViewById<View>(R.id.btn_spacing_increase)
        val btnSpacingReset = dialogView.findViewById<View>(R.id.btn_spacing_reset)
        val textSpacing = dialogView.findViewById<TextView>(R.id.text_spacing)

        fun updateSpacing(newSpacing: Double?) {
            preferences = preferences.copy(lineHeight = newSpacing)
            textSpacing.text = if (newSpacing != null) "%.1fx".format(newSpacing) else "1.2x"
            nav.submitPreferences(preferences)
            save()
        }
        textSpacing.text = "%.1fx".format(preferences.lineHeight ?: 1.2)

        btnSpacingDecrease.setOnClickListener {
            val current = preferences.lineHeight ?: 1.2
            updateSpacing((current - 0.1).coerceAtLeast(1.0))
        }
        btnSpacingIncrease.setOnClickListener {
            val current = preferences.lineHeight ?: 1.2
            updateSpacing((current + 0.1).coerceAtMost(2.5))
        }
        btnSpacingReset.setOnClickListener { updateSpacing(null) }

        // Margins
        val btnMarginDecrease = dialogView.findViewById<View>(R.id.btn_margin_decrease)
        val btnMarginIncrease = dialogView.findViewById<View>(R.id.btn_margin_increase)
        val btnMarginReset = dialogView.findViewById<View>(R.id.btn_margin_reset)
        val textMargins = dialogView.findViewById<TextView>(R.id.text_margins)

        fun updateMargins(newMargins: Double?) {
            preferences = preferences.copy(pageMargins = newMargins)
            textMargins.text = if (newMargins != null) "%.2fx".format(newMargins) else "1.00x"
            nav.submitPreferences(preferences)
            save()
        }
        textMargins.text = "%.2fx".format(preferences.pageMargins ?: 1.0)

        btnMarginDecrease.setOnClickListener {
            val current = preferences.pageMargins ?: 1.0
            updateMargins((current - 0.25).coerceAtLeast(0.5))
        }
        btnMarginIncrease.setOnClickListener {
            val current = preferences.pageMargins ?: 1.0
            updateMargins((current + 0.25).coerceAtMost(3.0))
        }
        btnMarginReset.setOnClickListener { updateMargins(null) }

        // ======== DISPLAY TAB ========

        val btnThemeLight = dialogView.findViewById<View>(R.id.btn_theme_light)
        val btnThemeSepia = dialogView.findViewById<View>(R.id.btn_theme_sepia)
        val btnThemeDark = dialogView.findViewById<View>(R.id.btn_theme_dark)
        val checkLight = dialogView.findViewById<View>(R.id.check_theme_light)
        val checkSepia = dialogView.findViewById<View>(R.id.check_theme_sepia)
        val checkDark = dialogView.findViewById<View>(R.id.check_theme_dark)

        fun updateThemeChecks(theme: Theme?) {
            checkLight.visibility = if (theme == Theme.LIGHT) View.VISIBLE else View.GONE
            checkSepia.visibility = if (theme == Theme.SEPIA) View.VISIBLE else View.GONE
            checkDark.visibility = if (theme == Theme.DARK || theme == null) View.VISIBLE else View.GONE
        }

        // Show current checkmark
        updateThemeChecks(preferences.theme)

        fun applyTheme(theme: Theme?) {
            preferences = preferences.copy(theme = theme)
            nav.submitPreferences(preferences)
            save()
            updateThemeChecks(theme)
        }

        btnThemeLight.setOnClickListener { applyTheme(Theme.LIGHT) }
        btnThemeSepia.setOnClickListener { applyTheme(Theme.SEPIA) }
        btnThemeDark.setOnClickListener { applyTheme(Theme.DARK) }

        val dialog = MaterialAlertDialogBuilder(activity)
            .setView(dialogView)
            .create()

        dialog.show()
    }
}
