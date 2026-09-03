package com.booksync

import java.io.File
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Tokens must not leave the device in a backup or a device transfer (issue #176).
 *
 * The 30-day refresh token and the 24-hour access token live unencrypted in
 * `files/datastore/booksync_prefs.preferences_pb`. The manifest opted into
 * backup with no extraction rules at all, so that file was eligible for Google
 * cloud backup **and** for Android 12+ device-to-device transfer: a transferred
 * image carries a live refresh token to another device with no login. Plaintext
 * on the device is fine — storage is FBE-encrypted at rest — it is the copy that
 * leaves that matters. Cloud backup probably already failed the 25 MB quota
 * because the media files were included, but device transfer has no quota, so
 * the exposure was live.
 *
 * The rules are written as an **include-list**, not the exclude-list the issue
 * proposed. Naming only `database` and `sharedpref` means everything else —
 * the DataStore holding tokens, the server URL and the device id, the downloaded
 * books, the diagnostic logs — is excluded *by default*. An exclude-list has to
 * be updated every time a new file appears and silently leaks whatever nobody
 * remembered; an include-list fails closed. That still lets reading progress,
 * font size and playback speed survive a restore.
 *
 * Read as text rather than parsed as XML: this asserts what a reviewer would
 * check by eye, and it must fail loudly if someone adds `domain="file"` back.
 */
class ManifestBackupRulesTest {

    private fun appFile(relative: String): File {
        var dir = File("").absoluteFile
        repeat(4) {
            for (candidate in listOf(File(dir, "app/$relative"), File(dir, relative))) {
                if (candidate.exists()) return candidate
            }
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("Could not locate $relative from ${File("").absolutePath}")
    }

    private val manifest by lazy { appFile("src/main/AndroidManifest.xml").readText() }

    @Test
    fun `the manifest declares both rule files`() {
        // Two are needed: dataExtractionRules covers API 31+, fullBackupContent
        // covers 26-30, and minSdk is 26.
        assertTrue(
            "android:dataExtractionRules is missing — API 31+ backup and device " +
                "transfer are unrestricted (issue #176).",
            manifest.contains("android:dataExtractionRules=\"@xml/data_extraction_rules\""),
        )
        assertTrue(
            "android:fullBackupContent is missing — API 26-30 backup is unrestricted.",
            manifest.contains("android:fullBackupContent=\"@xml/backup_rules\""),
        )
    }

    @Test
    fun `cloud backup and device transfer both restrict what leaves`() {
        val rules = appFile("src/main/res/xml/data_extraction_rules.xml").readText()

        // Both sections, because they are independent: omitting device-transfer
        // is exactly the hole that made this live, since it has no size quota.
        assertTrue("<cloud-backup> section missing", rules.contains("<cloud-backup"))
        assertTrue("<device-transfer> section missing", rules.contains("<device-transfer"))

        assertTrue(
            "the rules must be an include-list, so anything new is excluded by default",
            rules.contains("<include"),
        )
        assertFalse(
            "domain=\"file\" would put files/datastore — the tokens — back in the backup",
            rules.contains("domain=\"file\""),
        )
    }

    @Test
    fun `the legacy rules restrict the same way`() {
        val rules = appFile("src/main/res/xml/backup_rules.xml").readText()
        assertTrue("<full-backup-content> root missing", rules.contains("<full-backup-content"))
        assertTrue(
            "the legacy rules must be an include-list too",
            rules.contains("<include"),
        )
        assertFalse(
            "domain=\"file\" would put files/datastore — the tokens — back in the backup",
            rules.contains("domain=\"file\""),
        )
    }

    @Test
    fun `local progress still survives a restore`() {
        // The point of an include-list over allowBackup="false": the user comes
        // back logged out but keeps reading progress and reader preferences.
        for (name in listOf("data_extraction_rules.xml", "backup_rules.xml")) {
            val rules = appFile("src/main/res/xml/$name").readText()
            assertTrue(
                "$name should keep the Room database so reading progress survives",
                rules.contains("domain=\"database\""),
            )
            assertTrue(
                "$name should keep shared prefs so reader/player settings survive",
                rules.contains("domain=\"sharedpref\""),
            )
        }
    }
}
