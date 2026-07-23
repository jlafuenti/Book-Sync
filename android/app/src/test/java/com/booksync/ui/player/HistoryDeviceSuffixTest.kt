package com.booksync.ui.player

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Unit test for [historyDeviceSuffix], the pure helper that renders device
 * attribution in Android's native History tab (mirrors the web Session History
 * panel's device-name/device-id fallback logic from issue #54's Task 3).
 */
class HistoryDeviceSuffixTest {

    @Test
    fun `uses device name when present`() {
        assertEquals(" · from Kitchen Pixel", historyDeviceSuffix("Kitchen Pixel", "device-abc"))
    }

    @Test
    fun `falls back to device id when name is absent`() {
        assertEquals(" · from device-abc", historyDeviceSuffix(null, "device-abc"))
    }

    @Test
    fun `falls back to device id when name is blank`() {
        assertEquals(" · from device-abc", historyDeviceSuffix("   ", "device-abc"))
    }

    @Test
    fun `renders empty string when both are absent`() {
        assertEquals("", historyDeviceSuffix(null, null))
    }
}
