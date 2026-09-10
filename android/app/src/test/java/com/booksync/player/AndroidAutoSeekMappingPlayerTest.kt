package com.booksync.player

import androidx.media3.common.Player
import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Android Auto "previous/next" mapped to relative seek (see
 * [AudioPlayerService.AndroidAutoSeekMappingPlayer]).
 *
 * An audiobook is one long media item, so a head unit's skip buttons have no
 * next *track* to go to. Without this mapping they either do nothing or jump to
 * the end of the book; with it they rewind and fast-forward, which is what the
 * buttons mean to someone listening in a car.
 *
 * This is pinned because the class carries no behaviour of its own beyond a set
 * of overrides, and overrides fail silently: delete one, or let a media3 upgrade
 * remove the method it was overriding, and the compiler is happy while the
 * button quietly reverts to doing nothing. That is exactly what media3 1.11
 * caused — it dropped the deprecated `hasNext()`, `seekToPreviousWindow()` and
 * `seekToNextWindow()` from `Player`, and the surviving `*MediaItem` overrides
 * are what has to keep the behaviour. Head units are also the surface least
 * likely to be exercised by hand (#172), so a test is the only thing that
 * routinely looks at it.
 *
 * **Not covered here: the `getAvailableCommands()` override.** `Player.Commands`
 * stores its flags in a `FlagSet`, which is backed by
 * `android.util.SparseBooleanArray`. This module runs JVM unit tests against the
 * stub `android.jar` with `unitTests.isReturnDefaultValues = true`
 * (`app/build.gradle.kts`), so every method on that class is a no-op and a
 * `Commands` built in a unit test always comes back empty — `add()` appears to
 * do nothing whether the production code is right or wrong. An assertion there
 * fails against correct code, so covering it needs Robolectric or an
 * instrumentation test, not this file.
 */
class AndroidAutoSeekMappingPlayerTest {

    private lateinit var delegate: Player
    private lateinit var player: AudioPlayerService.AndroidAutoSeekMappingPlayer

    @Before
    fun setUp() {
        delegate = mockk(relaxed = true)
        every { delegate.availableCommands } returns Player.Commands.EMPTY
        player = AudioPlayerService.AndroidAutoSeekMappingPlayer(delegate)
    }

    @Test
    fun `next seeks forward rather than skipping to another track`() {
        player.seekToNext()

        verify { delegate.seekForward() }
        verify(exactly = 0) { delegate.seekToNext() }
    }

    @Test
    fun `previous seeks back rather than skipping to another track`() {
        player.seekToPrevious()

        verify { delegate.seekBack() }
        verify(exactly = 0) { delegate.seekToPrevious() }
    }

    /**
     * The media-item variants are the ones that survive media3 1.11. A head unit
     * that advertises `COMMAND_SEEK_TO_NEXT_MEDIA_ITEM` rather than
     * `COMMAND_SEEK_TO_NEXT` reaches the player through these instead.
     */
    @Test
    fun `the media item variants seek too`() {
        player.seekToNextMediaItem()
        player.seekToPreviousMediaItem()

        verify { delegate.seekForward() }
        verify { delegate.seekBack() }
        verify(exactly = 0) { delegate.seekToNextMediaItem() }
        verify(exactly = 0) { delegate.seekToPreviousMediaItem() }
    }

    /**
     * A head unit greys out a skip button when the player says there is nothing
     * to skip to. There never is — one media item — so both must claim there is,
     * or the mapping above is unreachable.
     */
    @Test
    fun `both directions always claim another item so the buttons stay enabled`() {
        assertTrue(player.hasNextMediaItem())
        assertTrue(player.hasPreviousMediaItem())
    }
}
