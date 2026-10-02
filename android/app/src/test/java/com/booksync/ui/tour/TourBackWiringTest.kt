package com.booksync.ui.tour

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Source guard for the walkthrough changes in issue #788 that live in composables and the
 * reader Activity, which the JVM suite cannot run: [TourController] decides what Back
 * navigates to and when a card is held back (tested in `TourControllerTest`); this pins that
 * each screen actually carries out its part.
 */
class TourBackWiringTest {

    private fun source(path: String): String {
        var dir = File("").absoluteFile
        repeat(4) {
            val candidate = File(dir, "app/src/main/java/com/booksync/$path")
            if (candidate.exists()) return candidate.readText()
            val direct = File(dir, "src/main/java/com/booksync/$path")
            if (direct.exists()) return direct.readText()
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("Could not locate $path from ${File("").absolutePath}")
    }

    private val navHost by lazy { source("ui/BookSyncNavigation.kt") }
    private val reader by lazy { source("ui/reader/ReaderActivity.kt") }
    private val sheet by lazy { source("ui/components/CardOverflowMenu.kt") }
    private val library by lazy { source("ui/library/LibraryScreen.kt") }
    private val overlay by lazy { source("ui/tour/TourOverlay.kt") }

    @Test
    fun `the nav host reopens the reader and the player over Main`() {
        val handler = navHost.substringAfter("tour.controller.nav.collect").substringBefore("LaunchedEffect(gateMessage)")
        val openReader = handler.substringAfter("is TourNav.OpenReader ->").substringBefore("is TourNav.")
        assertTrue(openReader.contains("Routes.reader(navEvent.pairId)"))
        assertTrue(openReader.contains("popUpTo(Routes.MAIN)"))
        val openPlayer = handler.substringAfter("is TourNav.OpenPlayer ->").substringBefore("TourNav.PopToMain")
        assertTrue(openPlayer.contains("Routes.player(navEvent.pairId)"))
        assertTrue(openPlayer.contains("popUpTo(Routes.MAIN)"))
    }

    @Test
    fun `the reader Activity closes itself for Back onto Details or the player`() {
        val handler = reader.substringAfter("tourController.nav.collect").substringBefore("setBarsVisible(!isBarVisible)")
        assertTrue(handler.contains("TourNav.CloseReader -> finish()"))
        assertTrue(handler.contains("is TourNav.OpenPlayer -> finish()"))
    }

    @Test
    fun `the card sheet closes itself for Back onto the Library`() {
        val collector = sheet.substringAfter("tour.controller.nav.collect").substringBefore("TourScreenSettled(TourScreen.Sheet")
        assertTrue(collector.contains("TourNav.CloseSheet"))
        assertTrue(collector.contains("onDismiss()"))
    }

    @Test
    fun `the card sheet makes room above its rows for a sheet step's card`() {
        // Issue #788: hosted over the sheet's own content, the card had no room above or
        // below the row it pointed at and landed on top of it.
        val content = sheet.substringAfter("ModalBottomSheet(").substringBefore("// ----- Confirmations -----")
        assertTrue(content.contains("Spacer(Modifier.height(SHEET_TOUR_CARD_ROOM))"))
        assertTrue(
            "the room sits inside the Box the overlay is matched to",
            content.indexOf("SHEET_TOUR_CARD_ROOM") < content.indexOf("Modifier.matchParentSize()"),
        )
        assertTrue("and the card is asked to use it", content.contains("preferCardAbove = true"))
        assertTrue(overlay.contains("cardPlacement(hole, screenSize, cardHeightPx, preferAbove = preferCardAbove)"))
    }

    @Test
    fun `Library reopens the sheet the tour asks for, once`() {
        assertTrue(library.contains(".sheetRequest"))
        val effect = library.substringAfter("LaunchedEffect(sheetRequest)").substringBefore("\n    }\n")
        assertTrue(effect.contains("overflowTarget ="))
        assertTrue(effect.contains("tour.controller.sheetRequestHandled()"))
    }

    @Test
    fun `a card being held back draws no scrim and no card`() {
        val body = overlay.substringAfter("fun TourOverlay(")
        val revealing = body.substringAfter("if (state.revealing) {").substringBefore("return")
        assertTrue(revealing.contains(".clickable("))
        assertTrue("the early return comes before any drawing", body.indexOf("if (state.revealing)") < body.indexOf("Canvas("))
    }
}
