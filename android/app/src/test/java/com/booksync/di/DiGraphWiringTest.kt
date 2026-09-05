package com.booksync.di

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * A `@Provides` function must not ask for the thing it provides (issue #147).
 *
 * This exists because of a crash that every layer of the build waved through.
 * The demo wiring briefly had a provider shaped like "the optional version of
 * `DemoSignIn`, built from the real one": a `@Provides` returning `DemoSignIn?`
 * that took a `Provider<DemoSignIn>` and called `get()` on it.
 *
 * It is not that. **Dagger's binding key ignores Kotlin nullability**, so
 * `DemoSignIn?` and `DemoSignIn` are the same key: that function *was* the
 * binding for `DemoSignIn`, and asking it for a `Provider<DemoSignIn>` re-entered
 * it. Every launch of a build with the demo settings died in
 * `Application.onCreate` with a `StackOverflowError` looping through
 * `provideDemoSignIn` -> `DoubleCheck.get` -> `SwitchingProvider`.
 *
 * Nothing caught it. Dagger's own cycle detection is defeated by the `Provider`
 * indirection — deferring a lookup is the documented way to *break* a legitimate
 * cycle, so it cannot assume this one is a mistake. `assembleDebug` succeeded.
 * Every JVM unit test passed, because a unit test constructs the classes itself
 * and never asks Hilt for the graph; this module has no Hilt test runner and no
 * instrumentation source set, so nothing in CI builds a component at all. The
 * first execution of that code path is on a device.
 *
 * So: a source-text guard, in the same spirit as `BuildConfigPinsTest` — blunt,
 * exact, and cheap. It reads the modules rather than the graph, and pins the one
 * shape that produced the crash.
 */
class DiGraphWiringTest {

    private fun diSources(): List<File> {
        // Gradle runs unit tests with the module directory as the working dir;
        // walking upward also covers being run from `android/`.
        var dir = File("").absoluteFile
        repeat(4) {
            for (candidate in listOf(
                File(dir, "app/src/main/java/com/booksync/di"),
                File(dir, "src/main/java/com/booksync/di"),
            )) {
                if (candidate.isDirectory) {
                    return candidate.listFiles { f: File -> f.name.endsWith(".kt") }
                        ?.sortedBy { it.name }
                        .orEmpty()
                }
            }
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("Could not locate com/booksync/di from ${File("").absolutePath}")
    }

    /** One `@Provides` function: where it lives, its parameters and return type. */
    private data class ProvidesFun(
        val file: String,
        val name: String,
        val params: String,
        val returns: String,
    )

    private val declaration = Regex(
        """@Provides\b[\s\S]{0,240}?\bfun\s+(\w+)\s*\(([\s\S]*?)\)\s*:\s*([^=\n{]+)""",
    )

    /**
     * Every `@Provides fun name(params): Return` in the DI sources.
     *
     * Deliberately a regex over source text rather than a parser: the failure
     * being guarded is a shape you can see by eye, and a half-written Kotlin
     * parser would be a bigger liability than the thing it checks. Comments are
     * stripped first, so the prose in this file and in `AppModule` cannot fail it.
     */
    private fun provides(): List<ProvidesFun> {
        val blockComment = Regex("""/\*.*?\*/""", RegexOption.DOT_MATCHES_ALL)
        return diSources().flatMap { file ->
            val text = file.readText()
                .replace(blockComment, "")
                .lineSequence()
                .filterNot { it.trimStart().startsWith("//") }
                .joinToString("\n")
            declaration.findAll(text).map {
                ProvidesFun(
                    file = file.name,
                    name = it.groupValues[1],
                    params = it.groupValues[2],
                    returns = it.groupValues[3],
                )
            }
        }
    }

    /** The bare type name, ignoring package qualification, generics and nullability. */
    private fun simpleName(type: String): String =
        type.trim().trimEnd('?').substringBefore('<').substringAfterLast('.').trim()

    /**
     * Whether [params] asks for [provided] — bare, or wrapped in `Provider`/`Lazy`.
     *
     * Anchored on `:` (a parameter's own type) or `<` (inside a wrapper), so
     * `impl: DemoSignInImpl` does not match `DemoSignIn`: the ordinary
     * implementation-to-interface binding must keep working, or the check gets
     * turned off the first time someone writes one.
     */
    private fun asksForItself(params: String, provided: String): Boolean =
        provided.isNotEmpty() &&
            Regex("""[:<]\s*(?:[\w.]*\.)?$provided\s*[?>,)\s]""").containsMatchIn("$params ")

    @Test
    fun `the DI sources are actually being read`() {
        // A silently-empty walk would make the assertion below pass for the wrong
        // reason — the failure mode test_android_no_personal_hosts.py guards
        // against on the server side.
        val files = diSources()
        assertTrue("found no files under com/booksync/di", files.isNotEmpty())
        assertTrue(
            "expected AppModule.kt among ${files.map { it.name }}",
            files.any { it.name == "AppModule.kt" },
        )
        assertTrue("parsed no @Provides functions", provides().size > 5)
    }

    @Test
    fun `no @Provides function depends on the type it provides`() {
        val offenders = provides().filter { asksForItself(it.params, simpleName(it.returns)) }

        assertTrue(
            "These @Provides functions ask for the type they provide, directly or " +
                "through a Provider/Lazy: " +
                offenders.joinToString { "${it.file}:${it.name} -> ${it.returns.trim()}" } +
                ". Dagger's key ignores nullability, so `fun f(x: Provider<T>): T?` " +
                "is a binding for T that depends on itself — it builds, and then " +
                "StackOverflowErrors on the device. Bind the implementation under a " +
                "different type, or inject the non-optional singleton and let it be " +
                "inert when it has nothing to do.",
            offenders.isEmpty(),
        )
    }

    @Test
    fun `the detector catches the shape that actually crashed`() {
        // Walking the real tree only says the tree is clean today; it cannot say
        // the check would notice if it were not.
        val sample = """
            @Provides
            @Singleton
            fun provideDemoSignIn(
                account: DemoAccount?,
                signIn: javax.inject.Provider<DemoSignIn>,
            ): DemoSignIn? = if (account == null) null else signIn.get()
        """.trimIndent()

        val match = declaration.find(sample)
        assertTrue("the @Provides parser did not match the crashing declaration", match != null)
        assertTrue(
            "the self-dependency check did not flag Provider<DemoSignIn> against a " +
                "DemoSignIn? return",
            asksForItself(match!!.groupValues[2], simpleName(match.groupValues[3])),
        )
    }

    @Test
    fun `a bare self-dependency is caught too`() {
        // The same mistake without the Provider wrapper. Dagger does reject this
        // one at compile time, but the check should not depend on that.
        assertTrue(asksForItself("existing: DemoSignIn, scope: CoroutineScope", "DemoSignIn"))
    }

    @Test
    fun `an implementation-to-interface binding is not flagged`() {
        // `fun x(impl: XImpl): X` is the ordinary way to bind an implementation.
        assertTrue(asksForItself("impl: DemoSignInImpl, scope: CoroutineScope", "DemoSignIn").not())
    }

    @Test
    fun `the demo wiring in particular is clean`() {
        // The specific regression, named: DemoSignIn is bound by its own @Inject
        // constructor and injected unconditionally. Only DemoAccount is optional.
        val demoProviders = provides().filter { "Demo" in it.returns }
        assertTrue(
            "expected AppModule to still provide the optional DemoAccount",
            demoProviders.any { simpleName(it.returns) == "DemoAccount" },
        )
        assertTrue(
            "AppModule must not provide DemoSignIn at all — its @Inject " +
                "constructor is the binding: ${demoProviders.map { it.name }}",
            demoProviders.none { simpleName(it.returns) == "DemoSignIn" },
        )
    }
}
