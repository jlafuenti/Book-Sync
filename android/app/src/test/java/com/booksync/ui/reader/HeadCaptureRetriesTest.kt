package com.booksync.ui.reader

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Issue #736: a head capture that cannot succeed - a fixed-layout or
 * unreadable resource - was retried on every page turn, forever. Each resource
 * now gets [HeadCaptureRetries.MAX_FAILURES] tries per layout.
 */
class HeadCaptureRetriesTest {

    @Test
    fun `a resource is tried until it has failed the maximum number of times`() {
        val r = HeadCaptureRetries()
        repeat(HeadCaptureRetries.MAX_FAILURES) {
            assertTrue(r.shouldTry("ch1.xhtml"))
            r.failed("ch1.xhtml")
        }
        assertFalse(r.shouldTry("ch1.xhtml"))
    }

    @Test
    fun `another resource still gets its own tries`() {
        val r = HeadCaptureRetries()
        repeat(HeadCaptureRetries.MAX_FAILURES) { r.failed("ch1.xhtml") }
        assertTrue(r.shouldTry("ch2.xhtml"))
    }

    @Test
    fun `a new layout starts every resource afresh`() {
        val r = HeadCaptureRetries()
        repeat(HeadCaptureRetries.MAX_FAILURES) { r.failed("ch1.xhtml") }
        r.reset()
        assertTrue(r.shouldTry("ch1.xhtml"))
    }
}
