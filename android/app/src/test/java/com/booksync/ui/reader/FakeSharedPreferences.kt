package com.booksync.ui.reader

import android.content.SharedPreferences

/**
 * Minimal in-memory [SharedPreferences] for JVM unit tests. This module has no
 * Robolectric (see app/build.gradle.kts, "no emulator or Robolectric in CI"),
 * so [ReaderProgressPrefsTest] needs a real, working implementation rather
 * than the stub `android.jar` methods `unitTests.isReturnDefaultValues`
 * quietly no-ops. Only the accessors [ReaderProgressPrefs] actually uses are
 * exercised, but every interface member is implemented so this type-checks as
 * a drop-in [SharedPreferences].
 */
internal class FakeSharedPreferences : SharedPreferences {
    private val values = mutableMapOf<String, Any?>()

    override fun getAll(): MutableMap<String, *> = values.toMutableMap()

    override fun getString(key: String?, defValue: String?): String? =
        values[key] as? String ?: defValue

    @Suppress("UNCHECKED_CAST")
    override fun getStringSet(key: String?, defValues: MutableSet<String>?): MutableSet<String>? =
        values[key] as? MutableSet<String> ?: defValues

    override fun getInt(key: String?, defValue: Int): Int = values[key] as? Int ?: defValue
    override fun getLong(key: String?, defValue: Long): Long = values[key] as? Long ?: defValue
    override fun getFloat(key: String?, defValue: Float): Float = values[key] as? Float ?: defValue
    override fun getBoolean(key: String?, defValue: Boolean): Boolean = values[key] as? Boolean ?: defValue
    override fun contains(key: String?): Boolean = values.containsKey(key)
    override fun edit(): SharedPreferences.Editor = FakeEditor()

    override fun registerOnSharedPreferenceChangeListener(
        listener: SharedPreferences.OnSharedPreferenceChangeListener?,
    ) = Unit

    override fun unregisterOnSharedPreferenceChangeListener(
        listener: SharedPreferences.OnSharedPreferenceChangeListener?,
    ) = Unit

    private inner class FakeEditor : SharedPreferences.Editor {
        private val pending = mutableMapOf<String, Any?>()
        private val removedKeys = mutableSetOf<String>()
        private var clearAll = false

        override fun putString(key: String?, value: String?): SharedPreferences.Editor =
            this.also { if (key != null) pending[key] = value }

        override fun putStringSet(key: String?, values: MutableSet<String>?): SharedPreferences.Editor =
            this.also { if (key != null) pending[key] = values }

        override fun putInt(key: String?, value: Int): SharedPreferences.Editor =
            this.also { if (key != null) pending[key] = value }

        override fun putLong(key: String?, value: Long): SharedPreferences.Editor =
            this.also { if (key != null) pending[key] = value }

        override fun putFloat(key: String?, value: Float): SharedPreferences.Editor =
            this.also { if (key != null) pending[key] = value }

        override fun putBoolean(key: String?, value: Boolean): SharedPreferences.Editor =
            this.also { if (key != null) pending[key] = value }

        override fun remove(key: String?): SharedPreferences.Editor =
            this.also { if (key != null) removedKeys += key }

        override fun clear(): SharedPreferences.Editor = this.also { clearAll = true }

        override fun commit(): Boolean {
            apply()
            return true
        }

        override fun apply() {
            if (clearAll) values.clear()
            removedKeys.forEach { values.remove(it) }
            values.putAll(pending)
        }
    }
}
