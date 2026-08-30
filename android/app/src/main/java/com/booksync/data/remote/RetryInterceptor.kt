package com.booksync.data.remote

import okhttp3.Interceptor
import okhttp3.Response
import java.io.IOException

/**
 * Retries transient failures, with two rules that were previously broken
 * (issue #218).
 *
 * **A 5xx is returned, not converted into an IOException.** The old code exhausted
 * its attempts and then threw `IOException("Request failed after N attempts")`,
 * discarding a response the server had actually sent. Callers treat an
 * IOException as "offline", so every server fault was reported to the user as a
 * network problem — the one diagnosis guaranteed to be wrong.
 *
 * **Attempts are spaced.** Three immediate retries is not a retry policy, it is
 * three extra requests aimed at a server already failing, arriving within
 * milliseconds. Backoff doubles from [baseDelayMs].
 */
class RetryInterceptor(
    private val maxRetries: Int = 3,
    private val baseDelayMs: Long = 250,
    private val sleep: (Long) -> Unit = Thread::sleep,
) : Interceptor {

    init {
        // Makes the fall-through below genuinely unreachable rather than
        // nearly so -- with 0 the loop never runs and the method would throw
        // the invented IOException this class exists to stop producing.
        require(maxRetries >= 1) { "maxRetries must be at least 1, was $maxRetries" }
    }

    override fun intercept(chain: Interceptor.Chain): Response {
        val request = chain.request()
        var lastException: IOException? = null

        repeat(maxRetries) { attempt ->
            val isLast = attempt == maxRetries - 1
            try {
                val response = chain.proceed(request)
                // Client errors are the caller's problem; retrying cannot help.
                if (response.isSuccessful || response.code < 500) return response
                // Out of attempts: hand back what the server said. It is more
                // useful than any exception this could invent.
                if (isLast) return response
                response.close()
            } catch (e: IOException) {
                lastException = e
                if (isLast) throw e
            }
            // A cancelled call -- a Coil load for a cover that scrolled away, or a
            // screen the user left -- should not hold a dispatcher slot sleeping.
            if (chain.call().isCanceled()) return@repeat
            try {
                sleep(baseDelayMs shl attempt)
            } catch (e: InterruptedException) {
                // WorkManager interrupts a worker's thread when it stops the work.
                // InterruptedException is not an IOException, so unhandled it
                // escapes as a type no caller here expects; and dropping it would
                // discard the cancellation entirely.
                Thread.currentThread().interrupt()
                throw IOException("Retry backoff interrupted", e)
            }
        }

        // Unreachable: the final attempt either returns or throws above. Kept so
        // the compiler is satisfied without inventing an error for a live server.
        throw lastException ?: IOException("Request failed after $maxRetries attempts")
    }
}
