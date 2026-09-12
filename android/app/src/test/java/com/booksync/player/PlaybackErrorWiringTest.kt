package com.booksync.player

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Source guards for issue #475: a playback failure is observed, and shown.
 *
 * The bug was an absence — `AudioPlayerService` registered a `Player.Listener`
 * implementing `onIsPlayingChanged`, `onMediaItemTransition`,
 * `onPositionDiscontinuity` and `onPlaybackStateChanged`, but not
 * `onPlayerError`. Nothing caught it, because an override that was never
 * written has nothing to assert against: the decision logic
 * ([PlaybackFailureTest]) and the state it feeds
 * (`PlayerViewModelPlaybackErrorTest`) can both be perfectly green while no
 * listener anywhere calls them.
 *
 * `AudioPlayerService` and `PlayerScreen` are excluded from Kover — service
 * glue and Compose — so, as with the heartbeat ([SeekFlushWiringTest]) and
 * pause ownership ([PauseOwnershipWiringTest]), the wiring is pinned by reading
 * the source. Delete the override and this fails; that is the whole job.
 */
class PlaybackErrorWiringTest {

    private fun source(relativePath: String): String {
        var dir = File("").absoluteFile
        repeat(4) {
            val candidate = File(dir, "app/src/main/java/$relativePath")
            if (candidate.exists()) return candidate.readText()
            val direct = File(dir, "src/main/java/$relativePath")
            if (direct.exists()) return direct.readText()
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("Could not locate $relativePath from ${File("").absolutePath}")
    }

    private fun codeLines(text: String): List<String> =
        text.lines().map { it.trim() }.filter { !it.startsWith("//") && !it.startsWith("*") }

    @Test
    fun `the service observes player errors`() {
        val service = codeLines(source("com/booksync/player/AudioPlayerService.kt"))
        assertTrue(
            "AudioPlayerService's Player.Listener must override onPlayerError. " +
                "Without it a failure is not merely unsurfaced — it is never " +
                "observed, and no layer above can react to it (issue #475).",
            service.any { it.contains("override fun onPlayerError") },
        )
    }

    /**
     * The car and the notification have no banner to draw. The service listener
     * is attached to both the local player and the Cast player, so its log line
     * is the only record a failure there leaves behind.
     */
    @Test
    fun `the service records the failure where it can be read back`() {
        val service = source("com/booksync/player/AudioPlayerService.kt")
        val body = service.substringAfter("override fun onPlayerError").substringBefore("\n            }")
        assertTrue(
            "onPlayerError must log through diagnosticLogger — a failure in " +
                "Android Auto leaves no other trace.",
            body.contains("diagnosticLogger"),
        )
        assertTrue(
            "The log must carry the error code; 'playback failed' with no code " +
                "is the same dead end in a different place.",
            body.contains("errorCode"),
        )
    }

    @Test
    fun `the player screen turns a player error into the failure state`() {
        val screen = codeLines(source("com/booksync/ui/player/PlayerScreen.kt"))
        assertTrue(
            "The MediaController listener must override onPlayerError — this is " +
                "the path that reaches the listener's eyes.",
            screen.any { it.contains("override fun onPlayerError") },
        )
        assertTrue(
            "It must route through onPlaybackError, which is where the remedy is " +
                "chosen from this book's download state.",
            screen.any { it.contains("onPlaybackError(error.errorCode)") },
        )
    }

    /**
     * Holding the state is not showing it. The screen has to draw the message
     * where the transport is, or the player still looks idle — which was the
     * actual complaint, not the missing callback.
     */
    @Test
    fun `the player screen draws the failure and its offer`() {
        val screen = source("com/booksync/ui/player/PlayerScreen.kt")
        val lines = codeLines(screen)
        assertTrue(
            "PlayerScreen must collect playbackError.",
            lines.any { it.contains("viewModel.playbackError.collectAsState()") },
        )
        assertTrue(
            "A RETRY failure must offer retryPlayback.",
            lines.any { it.contains("viewModel.retryPlayback()") },
        )
        assertTrue(
            "A REDOWNLOAD failure must be offered its own branch.",
            lines.any { it.contains("PlaybackRecovery.REDOWNLOAD") },
        )
        assertTrue(
            "That branch must call redownloadAudiobook, which deletes the broken " +
                "copy first — a plain downloadAudiobook() is skipped by the worker " +
                "for a book already marked downloaded, so the button succeeds and " +
                "fixes nothing.",
            lines.any { it.contains("viewModel.redownloadAudiobook()") },
        )
        assertTrue(
            "The banner must be drawn from the collected state, not just held.",
            lines.any { it.contains("playbackFailure?.let") },
        )
        assertTrue(
            "The message must be rendered.",
            lines.any { it.contains("failure.message") },
        )
    }

    /**
     * A Toast is what the download error uses, and it is wrong here: it fades
     * after a few seconds, carries no action, and leaves the transport looking
     * idle again — which is indistinguishable from the bug.
     */
    @Test
    fun `the failure is not shown as a disappearing toast`() {
        // The composable only — above it are the import and the ViewModel's own
        // `playbackFailureFor` call, which would make this pass for free.
        val composable = source("com/booksync/ui/player/PlayerScreen.kt")
            .substringAfter("\nfun PlayerScreen(")
        val mentions = codeLines(composable).filter { it.contains("playbackFailure") }
        assertTrue(
            "The composable must mention the failure at all — without this the " +
                "rest of this assertion is vacuous.",
            mentions.isNotEmpty(),
        )
        assertTrue(
            "The playback failure must be a persistent banner, not a Toast: a " +
                "Toast fades after a few seconds, carries no action, and leaves " +
                "the transport looking idle again — indistinguishable from the bug.",
            mentions.none { it.contains("Toast") },
        )
    }
}
