package com.booksync

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Issue #148: `targetSdk` was never declared, so the effective target silently
 * followed `compileSdk`. Two things go wrong with that.
 *
 *  1. Google Play refuses new apps and updates that target below API 36 from
 *     31 August 2026 (the deadline moves up one API level every year —
 *     https://developer.android.com/google/play/requirements/target-sdk). An
 *     inherited target is one nobody reviews, so it drifts below the floor
 *     without anyone noticing until an upload is rejected.
 *  2. `targetSdk` is a behaviour switch, not a version number. Bumping
 *     `compileSdk` for an unrelated reason would silently change permission
 *     prompts, foreground-service rules, edge-to-edge and orientation handling
 *     at runtime. Pinning it means that change has to be deliberate.
 *
 * Gradle configuration is not reachable from a JVM unit test — there is no
 * Robolectric or AGP test fixture in this module — so this reads the build
 * script, the same blunt-but-exact instrument `SyncWiringTest` uses. It pins the
 * declaration, not the behaviour; the behaviour needs an API 36 device.
 */
class BuildConfigPinsTest {

    /** Lowest target Play accepts for new uploads as of 31 Aug 2026. */
    private val requiredSdk = 36

    private fun buildScript(): String {
        // Gradle runs unit tests with the module directory as the working dir;
        // walking upward also covers being run from `android/`.
        var dir = File("").absoluteFile
        repeat(4) {
            for (candidate in listOf(File(dir, "app/build.gradle.kts"), File(dir, "build.gradle.kts"))) {
                if (candidate.exists() && candidate.readText().contains("compileSdk")) {
                    return candidate.readText()
                }
            }
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("Could not locate app/build.gradle.kts from ${File("").absolutePath}")
    }

    /**
     * Uncommented `<name> = <int>` assignment, or null if absent.
     *
     * Block comments are stripped before line comments, and the direction of the
     * failure matters: `find` takes the first match, so a commented-out older
     * value above the live one wins. A block-commented `targetSdk = 40` sitting
     * above a live `targetSdk = 34` would otherwise make this test pass while the
     * app shipped below Play's floor.
     */
    private fun sdkLevel(name: String): Int? {
        val pattern = Regex("""^\s*$name\s*=\s*(\d+)""", RegexOption.MULTILINE)
        val blockComment = Regex("""/\*.*?\*/""", RegexOption.DOT_MATCHES_ALL)
        return buildScript()
            .replace(blockComment, "")
            .lineSequence()
            .filterNot { it.trimStart().startsWith("//") }
            .joinToString("\n")
            .let { pattern.find(it) }
            ?.groupValues
            ?.get(1)
            ?.toInt()
    }

    @Test
    fun `targetSdk is declared explicitly and meets Play's floor`() {
        val target = sdkLevel("targetSdk")
        assertTrue(
            "app/build.gradle.kts declares no targetSdk, so the effective target " +
                "silently follows compileSdk. Play requires >= $requiredSdk.",
            target != null,
        )
        assertTrue(
            "targetSdk is $target; Play refuses new apps and updates below " +
                "$requiredSdk from 31 Aug 2026.",
            target!! >= requiredSdk,
        )
    }

    @Test
    fun `compileSdk is at least as high as targetSdk`() {
        val compile = sdkLevel("compileSdk")
        assertTrue("app/build.gradle.kts declares no compileSdk", compile != null)
        assertTrue(
            "compileSdk is $compile; it must be >= $requiredSdk to compile against " +
                "the APIs targetSdk $requiredSdk implies.",
            compile!! >= requiredSdk,
        )
    }
}
