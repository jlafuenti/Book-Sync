package com.booksync.data.remote

import okhttp3.logging.HttpLoggingInterceptor

/**
 * Whether OkHttp writes request lines to logcat (issue #231).
 *
 * Release builds shipped with `BASIC` logging on every client. Nothing stripped
 * it: there is no `BuildConfig.DEBUG` gate anywhere in `main`, and
 * `proguard-rules.pro` has no `-assumenosideeffects` rule for `android.util.Log`,
 * so R8 leaves the calls in the minified build.
 *
 * What that exposed is reading behaviour — `/api/library/search?q=…` and every
 * dictionary look-up — to anyone with adb, to bug-report bundles attached to Play
 * reviews, and to OEM diagnostics. Not to other apps, which have not been able to
 * read another app's logcat since Android 4.1.
 *
 * A function rather than an inline `if` at each call site because there are three
 * clients (main, refresh, dictionary) and the interesting failure is a *new*
 * fourth one being added without the gate. One name to grep for, and one test.
 */
fun httpLoggingLevel(isDebug: Boolean): HttpLoggingInterceptor.Level =
    if (isDebug) HttpLoggingInterceptor.Level.BASIC else HttpLoggingInterceptor.Level.NONE
