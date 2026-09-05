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

    // -----------------------------------------------------------------------
    // Release build configuration (issues #144, #145).
    //
    // None of this is executed by a unit test — it is Gradle configuration and
    // R8 input. CI compiles it via `bundleRelease`, which catches a *broken*
    // config, but not a silently *removed* one: delete the signing block and the
    // build still succeeds, unsigned, and the first anyone knows is a rejected
    // Play upload. So these read the files, the same blunt instrument the SDK
    // pins above already use.
    // -----------------------------------------------------------------------

    private fun proguardRules(): String {
        var dir = File("").absoluteFile
        repeat(4) {
            for (candidate in listOf(
                File(dir, "app/proguard-rules.pro"),
                File(dir, "proguard-rules.pro"),
            )) {
                if (candidate.exists()) return candidate.readText()
            }
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("Could not locate proguard-rules.pro")
    }

    @Test
    fun `release signing is configured, and reads the keystore from local properties`() {
        val script = buildScript()

        assertTrue(
            "No signingConfigs block: assembleRelease produces an unsigned APK and " +
                "bundleRelease an unsigned AAB, so Play submission cannot start (#144).",
            script.contains("signingConfigs"),
        )
        assertTrue(
            "The signing config must read tandem.signing.storeFile through " +
                "tandemSetting(), so the keystore path and passwords stay in " +
                "android/local.properties and never enter the repository.",
            script.contains("tandem.signing.storeFile"),
        )
        for (key in listOf("tandem.signing.storePassword", "tandem.signing.keyAlias", "tandem.signing.keyPassword")) {
            assertTrue("The signing config must read $key.", script.contains(key))
        }
    }

    @Test
    fun `signing is applied only when a keystore is actually present`() {
        // A clean clone has no local.properties. If the release build type
        // referenced the signing config unconditionally, Gradle would fail at
        // configuration time on a missing storeFile — breaking CI and every
        // fresh contributor. The guard is what lets `bundleRelease` run unsigned.
        val script = buildScript()

        assertTrue(
            "The release signingConfig must be applied conditionally on the " +
                "keystore file existing — a clean clone must still build unsigned.",
            script.contains("storeFile") && Regex("""\.exists\(\)""").containsMatchIn(script),
        )
    }

    @Test
    fun `R8 keeps the dependencies that ship no consumer rules`() {
        // Media3, Cast, Room and Retrofit each ship their own proguard.txt inside
        // the artifact, so R8 already knows about them. These three ship nothing,
        // and they are the reader, the cast server and the HTML parser — the
        // paths a stripped class would break in someone's hands rather than at
        // build time (#145).
        val rules = proguardRules()

        assertTrue(
            "Readium ships no consumer ProGuard rules; without a keep, R8 can " +
                "strip the reader's reflective/serialized surface.",
            rules.contains("org.readium."),
        )
        assertTrue(
            "nanohttpd ships no consumer rules and LocalCastHttpServer extends " +
                "fi.iki.elonen.NanoHTTPD — casting depends on it.",
            rules.contains("fi.iki.elonen."),
        )
    }

    @Test
    fun `resource shrinking is on for release`() {
        // A no-op without minification and a real size saving with it, so there
        // is no reason to ship minified-but-unshrunk.
        assertTrue(
            "isShrinkResources should be enabled alongside isMinifyEnabled.",
            Regex("""isShrinkResources\s*=\s*true""").containsMatchIn(buildScript()),
        )
    }

    // -----------------------------------------------------------------------
    // The public demo account (issue #147).
    //
    // Play's reviewer cannot use Tandem without a server and an account, so the
    // first-run screen offers a "Try the demo" button — but only in a build that
    // was given a demo to point at. The three values are machine-local build
    // settings, defaulting to empty exactly like `tandem.defaultServerUrl`
    // (issue #58), so a clean clone ships no hostname, no username, no password,
    // and therefore no button.
    //
    // A hardcoded literal here would be all three of those things at once, in a
    // public repository, in the one file nobody re-reads. Hence the assertion is
    // not "the default is empty" (a build with a local.properties would fail it)
    // but "the value comes from tandemSetting(), whose default is empty".
    // -----------------------------------------------------------------------

    /** Gradle property name → BuildConfig field, for the demo credentials. */
    private val demoSettings = mapOf(
        "tandem.demoUrl" to "DEMO_URL",
        "tandem.demoUser" to "DEMO_USER",
        "tandem.demoPassword" to "DEMO_PASSWORD",
    )

    /** The `buildConfigField` value expression declared for [field], or null. */
    private fun buildConfigValue(field: String): String? =
        Regex(""""$field"\s*,\s*(.+)""")
            .find(buildScript())
            ?.groupValues
            ?.get(1)
            ?.trim()
            ?.trimEnd(',')

    @Test
    fun `the demo account is read from build settings, never hardcoded`() {
        for ((property, field) in demoSettings) {
            val value = buildConfigValue(field)
            assertTrue(
                "app/build.gradle.kts declares no BuildConfig.$field. The " +
                    "first-run demo button (#147) needs all three of " +
                    "${demoSettings.values}.",
                value != null,
            )
            assertTrue(
                "BuildConfig.$field is declared as `$value`. It must come from " +
                    "tandemSetting(\"$property\"), whose default is empty — a " +
                    "clean clone then ships no demo host and no demo " +
                    "credentials, and the first-run screen shows no demo " +
                    "button (#147, #58).",
                value!!.contains("""tandemSetting("$property")"""),
            )
        }
    }

    @Test
    fun `no demo credential is written into the build script as a literal`() {
        // The failure this guards against is a developer "just for now" pasting
        // the live demo password in to try the button out. The repository is
        // public: a password in a committed file is published the moment it is
        // pushed, and stays in the history after it is deleted.
        //
        // Take the interpolation out and only the quoting may remain, so a
        // spliced-in fallback ("...tandemSetting(x) ?: "demo123"...") fails here
        // rather than shipping.
        for ((property, field) in demoSettings) {
            val stripped = buildConfigValue(field)
                .orEmpty()
                .replace("""${'$'}{tandemSetting("$property")}""", "")
            assertTrue(
                "BuildConfig.$field is declared as more than the " +
                    "tandemSetting(\"$property\") interpolation — `$stripped` is " +
                    "left over once it is removed. Put the value in " +
                    "android/local.properties (gitignored), never in the build " +
                    "script.",
                stripped.isNotEmpty() && stripped.all { it == '"' || it == '\\' },
            )
        }
    }

    @Test
    fun `CI compiles the release variant`() {
        // The release path rotted precisely because nothing ever built it: no
        // release artifact had ever been produced in this tree. R8 running on
        // every PR is what turns a missing keep rule into a red build instead of
        // a crash after upload.
        var dir = File("").absoluteFile
        var workflow: File? = null
        repeat(5) {
            val candidate = File(dir, ".github/workflows/android-tests.yml")
            if (candidate.exists()) { workflow = candidate; return@repeat }
            dir = dir.parentFile ?: return@repeat
        }
        val text = workflow?.readText()
            ?: throw AssertionError("Could not locate .github/workflows/android-tests.yml")

        assertTrue(
            "android-tests.yml must run a release-variant build (bundleRelease) so " +
                "R8 runs in CI. Without it, keep-rule gaps surface only after upload.",
            text.contains("bundleRelease") || text.contains("assembleRelease"),
        )
    }
}
