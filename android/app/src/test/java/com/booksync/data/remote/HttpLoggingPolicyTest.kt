package com.booksync.data.remote

import okhttp3.logging.HttpLoggingInterceptor
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * HTTP logging is a debug-build feature (issue #231).
 *
 * `HttpLoggingInterceptor` at `BASIC` writes the method and full URL of every
 * request to logcat, and release builds shipped with it on: there is no
 * `BuildConfig.DEBUG` gate anywhere in `main`, and `proguard-rules.pro` carries
 * no `-assumenosideeffects` rule, so R8 strips nothing.
 *
 * Other apps cannot read logcat, so this is not a direct leak to a malicious
 * app. It does leak to anyone with adb, to bug-report bundles a user attaches to
 * a Play review, and to OEM diagnostics — and what leaks is reading behaviour:
 * `/api/library/search?q=…` and every dictionary look-up.
 *
 * `BASIC` prints no headers, so the bearer token itself was never in there.
 */
class HttpLoggingPolicyTest {

    @Test
    fun `release builds log nothing`() {
        assertEquals(HttpLoggingInterceptor.Level.NONE, httpLoggingLevel(isDebug = false))
    }

    @Test
    fun `debug builds keep the request line`() {
        assertEquals(HttpLoggingInterceptor.Level.BASIC, httpLoggingLevel(isDebug = true))
    }
}
