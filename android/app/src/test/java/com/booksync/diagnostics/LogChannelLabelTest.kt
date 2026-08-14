package com.booksync.diagnostics

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Issue #58: the diagnostics UI, its foreground notification and the share-email
 * subject are all built from [LogChannel.label], and the app channel still read
 * "BookSync App" after the product was renamed to Tandem. Pin the labels so a
 * user-visible surface can't drift back to the old name.
 */
class LogChannelLabelTest {

    @Test
    fun `app channel is labelled Tandem`() {
        assertEquals("Tandem App", LogChannel.APP.label)
    }

    @Test
    fun `auto channel keeps its platform name`() {
        // "Android Auto" is the platform's name, not ours — it must not be renamed.
        assertEquals("Android Auto", LogChannel.AUTO.label)
    }
}
