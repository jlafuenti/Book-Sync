package com.booksync.ui.reader

/**
 * How often the reader may try to capture the live page's head for the page
 * count (issue #736). A capture that cannot succeed - a fixed-layout or
 * unreadable resource - used to be retried on every page turn, forever. Each
 * resource now gets [MAX_FAILURES] tries per layout; [reset] when the layout
 * changes (settings, size) starts every resource afresh.
 */
class HeadCaptureRetries {
    private val failures = mutableMapOf<String, Int>()

    fun shouldTry(href: String): Boolean = (failures[href] ?: 0) < MAX_FAILURES

    fun failed(href: String) {
        failures[href] = (failures[href] ?: 0) + 1
    }

    fun reset() = failures.clear()

    companion object {
        const val MAX_FAILURES = 3
    }
}
