package com.booksync.ui.reader

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Regression test for issue #163: ReaderActivity's process-death guard
 * (`savedInstanceState != null && publication == null` → finish) fires on ANY
 * recreation, and without `android:configChanges` the system recreates the
 * activity on rotation, dark-mode toggle, font-scale change, locale change
 * and split-screen — so the book snapped shut on the first rotation, for
 * every user.
 *
 * The fix keeps the guard (the reflective EpubNavigatorFragment crash it
 * prevents is real) and stops the recreations instead: the activity declares
 * the config changes and handles them in place — Readium's WebView re-lays
 * itself out, and the reader's theme is its own preference, not uiMode.
 *
 * No Robolectric in this module, so this reads the manifest
 * (SyncWiringTest's source-guard precedent). Do NOT "fix" the underlying
 * issue with an orientation lock: Android 16 ignores orientation
 * restrictions on large screens and the guard would fire anyway.
 */
class ReaderManifestTest {

    private fun manifest(): String {
        var dir = File("").absoluteFile
        repeat(4) {
            val candidate = File(dir, "app/src/main/AndroidManifest.xml")
            if (candidate.exists()) return candidate.readText()
            val direct = File(dir, "src/main/AndroidManifest.xml")
            if (direct.exists()) return direct.readText()
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("AndroidManifest.xml not found from ${File("").absolutePath}")
    }

    @Test
    fun `ReaderActivity declares the config changes that used to close the book`() {
        val text = manifest()
        val activityBlock = text.substringAfter(".ui.reader.ReaderActivity")
            .substringBefore("/>")
        val configChanges = Regex("android:configChanges=\"([^\"]*)\"")
            .find(activityBlock)?.groupValues?.get(1)
            ?: throw AssertionError(
                "ReaderActivity has no android:configChanges — every rotation " +
                    "recreates it and the process-death guard closes the book (issue #163)."
            )

        for (required in listOf(
            "orientation", "screenSize", "smallestScreenSize",
            "screenLayout", "uiMode", "fontScale", "density",
        )) {
            assertTrue(
                "ReaderActivity's configChanges must include '$required' (has: $configChanges)",
                configChanges.split("|").contains(required),
            )
        }
    }

    @Test
    fun `nothing locks the reader's orientation`() {
        assertTrue(
            "Do not fix rotation with an orientation lock — Android 16 ignores " +
                "orientation restrictions on large screens (issue #163).",
            !manifest().contains("screenOrientation"),
        )
    }
}
