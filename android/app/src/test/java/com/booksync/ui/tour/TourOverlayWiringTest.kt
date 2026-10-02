package com.booksync.ui.tour

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Source guard for [TourOverlay]'s drawing, which the JVM suite cannot render (issue #764): the
 * decisions live in [spotlightRing] and [isSelectionStep], tested on their own; this pins that the
 * overlay actually uses them.
 */
class TourOverlayWiringTest {

    private val overlay: String by lazy {
        var dir = File("").absoluteFile
        repeat(4) {
            val candidate = File(dir, "app/src/main/java/com/booksync/ui/tour/TourOverlay.kt")
            if (candidate.exists()) return@lazy candidate.readText()
            val direct = File(dir, "src/main/java/com/booksync/ui/tour/TourOverlay.kt")
            if (direct.exists()) return@lazy direct.readText()
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("Could not locate TourOverlay.kt from ${File("").absolutePath}")
    }

    @Test
    fun `both selection steps leave the page unblocked`() {
        assertTrue(overlay.contains("isSelectionStep(state.step.id) && state.resolution == AnchorResolution.Found"))
        assertTrue(!overlay.contains("state.step.id == READER_SELECTION_STEP_ID"))
    }

    @Test
    fun `the spotlight is outlined in the accent colour after the hole is cleared`() {
        val canvas = overlay.substringAfter("graphicsLayer(alpha = 0.99f)").substringBefore("if (blockEverything || hole == null)")
        assertTrue(canvas.contains("spotlightRing(hole, blockNothing"))
        assertTrue(canvas.contains("BlendMode.Clear"))
        assertTrue("the ring is drawn after the hole is cleared", canvas.indexOf("BlendMode.Clear") < canvas.indexOf("ringColor"))
        assertTrue(canvas.contains("style = Stroke("))
        assertTrue("the ring takes the card's accent colour", overlay.contains("val ringColor = colors.accent"))
    }
}
