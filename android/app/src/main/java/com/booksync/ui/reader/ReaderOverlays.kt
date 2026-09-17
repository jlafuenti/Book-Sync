package com.booksync.ui.reader

import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import com.booksync.data.remote.dto.TranscriptionStatus
import com.booksync.ui.components.TranscriptionStatusDialog
import com.booksync.ui.theme.BookSyncTheme
import com.booksync.ui.tour.LocalTourRegistry
import com.booksync.ui.tour.TourAnchorRegistry
import com.booksync.ui.tour.TourController
import com.booksync.ui.tour.TourOverlay
import com.booksync.ui.tour.TourScreen
import com.booksync.ui.tour.TourState

/**
 * The reader's Compose overlay content (issue #597 Track C), hosted from
 * [R.id.overlay_host] — a full-screen `ComposeView` above the Readium
 * navigator that passes touches through when this renders nothing.
 * Previously this spot held only [TranscriptionStatusDialog]
 * (`transcription_dialog_host`, issue #536); it now also renders the
 * walkthrough's [TourOverlay] whenever a tour step is spotlighting something
 * in the reader or the player, mirroring the outer `NavHost`'s own
 * `TourOverlay` placement in `BookSyncNavigation`.
 *
 * The `Reader` **or** `Player` check on [TourState.Running.step] matters:
 * the tour pops the reader's result back through `ReaderScreen` mid-step
 * while the pair opens for real audio playback, so a step can still be
 * `TourScreen.Player` for a moment while this Activity is still the one on
 * screen. Rendering the overlay for either keeps the card from flashing off
 * during that hand-off.
 */
@Composable
fun ReaderOverlays(
    pendingSwitchStatus: TranscriptionStatus?,
    canTranscribeSwitch: Boolean,
    isOnline: Boolean,
    onContinueSwitch: () -> Unit,
    onTranscribeSwitch: () -> Unit,
    onDismissSwitch: () -> Unit,
    tourController: TourController,
    tourRegistry: TourAnchorRegistry,
) {
    CompositionLocalProvider(LocalTourRegistry provides tourRegistry) {
        BookSyncTheme {
            if (pendingSwitchStatus != null) {
                TranscriptionStatusDialog(
                    status = pendingSwitchStatus,
                    canTranscribe = canTranscribeSwitch,
                    isOnline = isOnline,
                    continueLabel = "Switch anyway",
                    onContinue = onContinueSwitch,
                    onTranscribe = onTranscribeSwitch,
                    onDismiss = onDismissSwitch,
                )
            }

            val tourState by tourController.state.collectAsState()
            val running = tourState as? TourState.Running
            if (running != null && running.step.screen.let { it == TourScreen.Reader || it == TourScreen.Player }) {
                TourOverlay(
                    state = running,
                    onNext = tourController::next,
                    onBack = tourController::back,
                    onSkip = tourController::skip,
                    onQuit = tourController::quit,
                )
            }
        }
    }
}
