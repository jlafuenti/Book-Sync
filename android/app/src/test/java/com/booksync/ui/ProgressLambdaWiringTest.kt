package com.booksync.ui

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Source guard for issue #765: no `!!` inside a progress indicator's `progress = { … }` lambda.
 *
 * Compose re-reads that lambda on its own schedule — the semantics pass, a redraw — not only
 * during the composition that drew the indicator. When the lambda reads a nullable state
 * delegate (`by …collectAsState()`), it sees the *live* value, so a state that goes back to
 * `null` (a download finishing) throws before recomposition removes the indicator. The player's
 * download bar crashed the app exactly that way. Capture the value in a local first and let the
 * lambda read the local.
 *
 * The crash itself needs a device, so the rule is pinned by reading every source file.
 */
class ProgressLambdaWiringTest {

    private fun sourceRoot(): File {
        var dir = File("").absoluteFile
        repeat(4) {
            val candidate = File(dir, "app/src/main/java")
            if (candidate.isDirectory) return candidate
            val direct = File(dir, "src/main/java")
            if (direct.isDirectory) return direct
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("Could not locate src/main/java from ${File("").absolutePath}")
    }

    private val progressLambda = Regex("""progress\s*=\s*\{([^{}]*)\}""")

    @Test
    fun `no progress lambda dereferences a nullable state with !!`() {
        val lambdas = sourceRoot().walkTopDown()
            .filter { it.isFile && it.extension == "kt" }
            .flatMap { file ->
                progressLambda.findAll(file.readText()).map { file.name to it.groupValues[1].trim() }
            }
            .toList()

        // The guard is only worth something if it sees the indicators it is meant to police.
        assertTrue(
            "expected to find the app's progress lambdas, found ${lambdas.size}",
            lambdas.size >= 5,
        )
        val offenders = lambdas.filter { (_, body) -> "!!" in body }
        assertTrue(
            "progress lambdas must read a captured local, not `state!!` (issue #765): $offenders",
            offenders.isEmpty(),
        )
    }
}
