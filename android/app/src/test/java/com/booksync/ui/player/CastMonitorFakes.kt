package com.booksync.ui.player

import com.booksync.cast.CastSessionMonitor
import com.booksync.player.CastFailure
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.flow.MutableStateFlow

/**
 * A relaxed [CastSessionMonitor] whose `castFailure` is a real flow.
 *
 * `PlayerViewModel` collects `castFailure` for its whole life (issue #932). A
 * plain `mockk(relaxed = true)` hands back a mocked `StateFlow`, whose
 * `collect` cannot return normally and throws `KotlinNothingValueException`
 * the first time the view model starts.
 */
internal fun relaxedCastMonitor(
    failure: MutableStateFlow<CastFailure?> = MutableStateFlow(null),
): CastSessionMonitor = mockk<CastSessionMonitor>(relaxed = true).also {
    every { it.castFailure } returns failure
}
